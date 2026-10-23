"""Week 6 (stretch): lead stages reported back to Meta (Conversions API)."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Lead, MetaEvent
from app.db.session import session_scope
from app.dev import payloads as p
from app.worker.processor import run_once
from tests.conftest import signed_post

LEADGEN_ID = "1234567890123456"  # Meta lead IDs are 15-17 digits
PHONE = "919811055555"


@pytest.fixture
def feedback_on(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    def set_(**env: str) -> None:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()

    set_(META_FEEDBACK_ENABLED="true", WHATSAPP_BUSINESS_ACCOUNT_ID="900000000000001")
    yield set_
    get_settings.cache_clear()


def _events(**where: str) -> list[MetaEvent]:
    with session_scope() as s:
        q = select(MetaEvent).order_by(MetaEvent.created_at)
        for k, v in where.items():
            q = q.where(getattr(MetaEvent, k) == v)
        return list(s.scalars(q))


def _lead(**where: str) -> Lead:
    with session_scope() as s:
        q = select(Lead)
        for k, v in where.items():
            q = q.where(getattr(Lead, k) == v)
        return s.scalar(q)


def test_off_by_default(client: TestClient, catalog, auth_headers) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/meta/page", p.leadgen(leadgen_id=LEADGEN_ID))
    run_once()
    lead = _lead(meta_lead_id=LEADGEN_ID)
    client.patch(f"/api/leads/{lead.lead_id}", json={"status": "converted"}, headers=auth_headers)
    assert _events() == []
    assert client.get("/api/meta-events", headers=auth_headers).json()["enabled"] is False


def test_lead_form_stages_as_crm_events(client: TestClient, catalog, auth_headers, feedback_on) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/meta/page", p.leadgen(leadgen_id=LEADGEN_ID))
    run_once()
    lead = _lead(meta_lead_id=LEADGEN_ID)
    [first] = _events()
    assert first.kind == "crm" and first.status == "logged"  # fake mode: recorded, not sent
    assert first.payload == {
        "event_name": "Lead",
        "event_time": first.payload["event_time"],
        "action_source": "system_generated",
        "user_data": {"lead_id": int(LEADGEN_ID)},
        "custom_data": {"event_source": "crm", "lead_event_source": "Lead desk"},
    }
    for status in ("contacted", "converted", "converted"):
        client.patch(f"/api/leads/{lead.lead_id}", json={"status": status}, headers=auth_headers)
    run_once()
    assert [e.event_name for e in _events()] == ["Lead", "Contacted", "Bought"]  # each stage once
    listed = client.get("/api/meta-events", headers=auth_headers).json()
    assert listed["counts_by_status"] == {"logged": 3}


def test_ctwa_lead_gets_messaging_events_with_order_value(
    client: TestClient, catalog, auth_headers, feedback_on
) -> None:  # noqa: ANN001
    signed_post(
        client,
        "/webhooks/whatsapp",
        p.whatsapp_ctwa(PHONE, "6L air fryer price?", ctwa_clid="ARAkLkA8rmlFeiCktEJQ"),
    )
    run_once()
    signed_post(
        client,
        "/webhooks/whatsapp",
        p.whatsapp_text(PHONE, "Need 2, delivery Chennai this week, budget 15000", at=int(time.time()) + 5),
    )
    run_once()
    lead = _lead(contact_phone="+" + PHONE)
    assert lead.status == "hot"
    [qualified] = _events(kind="messaging")
    assert qualified.payload["event_name"] == "QualifiedLead"
    assert qualified.payload["action_source"] == "business_messaging"
    assert qualified.payload["messaging_channel"] == "whatsapp"
    assert qualified.payload["user_data"] == {
        "whatsapp_business_account_id": "900000000000001",
        "ctwa_clid": "ARAkLkA8rmlFeiCktEJQ",
    }
    client.patch(f"/api/leads/{lead.lead_id}", json={"status": "converted"}, headers=auth_headers)
    purchase = _events(event_name="Purchase")[0]
    assert purchase.payload["custom_data"] == {"currency": "INR", "value": 13598.0}  # 2 x ₹6,799
    assert _events(kind="crm") == []  # not a lead-form lead


def test_purchase_skipped_when_value_unknown(client: TestClient, catalog, auth_headers, feedback_on) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_ctwa(PHONE, "hello", ctwa_clid="CLID1"))
    run_once()
    lead = _lead(contact_phone="+" + PHONE)
    client.patch(f"/api/leads/{lead.lead_id}", json={"status": "converted"}, headers=auth_headers)
    assert _events(event_name="Purchase") == []


def _live(feedback_on, monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:  # noqa: ANN001
    feedback_on(META_API_MODE="live", META_DATASET_ID="555000111222", META_CAPI_ACCESS_TOKEN="capi-token")
    seen: list[httpx.Request] = []
    real = httpx.Client

    def client_factory(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        def wrapped(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        return real(*a, transport=httpx.MockTransport(wrapped), **kw)

    monkeypatch.setattr("app.meta_feedback.httpx.Client", client_factory)
    return seen


def _queue_one(user_id: str, kind: str = "crm") -> str:
    from app.meta_feedback import queue_stage

    with session_scope() as s:
        messaging = kind == "messaging"
        lead = Lead(
            user_id=user_id,
            meta_lead_id=None if messaging else LEADGEN_ID,
            ctwa_clid="CLID9" if messaging else None,
        )
        s.add(lead)
        s.flush()
        [ev] = queue_stage(s, lead, "qualified")
        assert ev.kind == kind
        return ev.event_id


def test_live_sends_to_the_dataset(user, feedback_on, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app.meta_feedback import deliver_meta_events

    seen = _live(feedback_on, monkeypatch, lambda r: httpx.Response(200, json={"events_received": 1}))
    event_id = _queue_one(user.user_id, "messaging")
    assert deliver_meta_events() == {"sent": 1}
    req = seen[0]
    assert req.url.path == "/v25.0/555000111222/events" and req.url.params["access_token"] == "capi-token"
    body = json.loads(req.content)
    assert body["data"][0]["event_name"] == "QualifiedLead" and body["partner_agent"] == "Lead desk"
    with session_scope() as s:
        assert s.get(MetaEvent, event_id).status == "sent"


def test_live_retries_then_gives_up(user, feedback_on, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app.meta_feedback import deliver_meta_events

    answers = [httpx.Response(500, json={"error": {"message": "temporary", "code": 2}})] + [
        httpx.Response(400, json={"error": {"message": "Invalid lead_id", "code": 100}})
    ]
    _live(feedback_on, monkeypatch, lambda r: answers.pop(0))
    event_id = _queue_one(user.user_id)
    assert deliver_meta_events() == {"queued": 1}  # temporary error: tried again later
    with session_scope() as s:
        ev = s.get(MetaEvent, event_id)
        ev.next_attempt_at = ev.created_at
    assert deliver_meta_events() == {"failed": 1}
    with session_scope() as s:
        assert "Invalid lead_id" in s.get(MetaEvent, event_id).error


def test_live_without_dataset_fails_clearly(user, feedback_on, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app.meta_feedback import deliver_meta_events

    feedback_on(META_API_MODE="live", META_DATASET_ID="")
    event_id = _queue_one(user.user_id)
    assert deliver_meta_events() == {"failed": 1}
    with session_scope() as s:
        assert s.get(MetaEvent, event_id).error == "META_DATASET_ID is not set"


def test_erasing_the_lead_cancels_its_events(client: TestClient, catalog, auth_headers, feedback_on) -> None:  # noqa: ANN001
    feedback_on(META_API_MODE="live", META_DATASET_ID="555")  # stays queued (not delivered in this test)
    from app.meta_feedback import queue_stage

    with session_scope() as s:
        lead = Lead(user_id=catalog.user_id, meta_lead_id=LEADGEN_ID, name="Ravi")
        s.add(lead)
        s.flush()
        queue_stage(s, lead, "lead")
        lead_id = lead.lead_id
    assert client.delete(f"/api/leads/{lead_id}/personal-data", headers=auth_headers).status_code == 200
    [ev] = _events()
    assert ev.status == "cancelled" and ev.payload == {}


def test_lead_ids_that_are_not_meta_ids_are_ignored(user, feedback_on) -> None:  # noqa: ANN001
    from app.meta_feedback import queue_stage

    with session_scope() as s:
        lead = Lead(user_id=user.user_id, meta_lead_id="777000123")  # too short to be Meta's
        s.add(lead)
        s.flush()
        assert queue_stage(s, lead, "lead") == []


def test_whatsapp_events_need_the_business_account_id(user, feedback_on) -> None:  # noqa: ANN001
    from app.meta_feedback import queue_stage

    feedback_on(WHATSAPP_BUSINESS_ACCOUNT_ID="")
    with session_scope() as s:
        lead = Lead(user_id=user.user_id, ctwa_clid="CLID7")
        s.add(lead)
        s.flush()
        assert queue_stage(s, lead, "qualified") == []


def test_each_stage_is_reported_once_even_if_the_status_flips_back(
    client: TestClient, catalog, auth_headers, feedback_on
) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/meta/page", p.leadgen(leadgen_id=LEADGEN_ID))
    run_once()
    lead = _lead(meta_lead_id=LEADGEN_ID)
    for status in ("converted", "lost", "converted"):  # a mis-click corrected twice
        r = client.patch(f"/api/leads/{lead.lead_id}", json={"status": status}, headers=auth_headers)
        assert r.status_code == 200
    assert [e.event_name for e in _events()] == ["Lead", "Bought", "Lost"]
