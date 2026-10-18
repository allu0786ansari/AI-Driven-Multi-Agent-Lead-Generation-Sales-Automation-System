"""Week 5: personal-data erasure, analytics and costs, hot-label feedback."""

from __future__ import annotations

import json
import time
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.analytics import service as analytics
from app.auth.service import create_user
from app.db.base import utcnow
from app.db.enums import ConsentType, DeliveryStatus, LeadSource, LeadStatus, NotificationKind, Sender
from app.db.models import ConsentEvent, InboundEvent, Lead, LeadActivity, Message, Notification
from app.db.session import session_scope
from app.dev import payloads as p
from app.worker.processor import run_once
from tests.conftest import signed_post

PHONE = "919845098450"
OTHER = "919700070007"


def _lead(phone: str) -> Lead | None:
    with session_scope() as s:
        return s.scalar(select(Lead).where(Lead.contact_phone == "+" + phone))


# ------------------------------------------------------------------ erase


def test_erase_personal_data(client: TestClient, catalog, auth_headers) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "kettle price?", name="Ravi Kumar"))
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(OTHER, "mixer price?", name="Other Person"))
    run_once()
    signed_post(
        client, "/webhooks/whatsapp",
        p.whatsapp_text(PHONE, "please call me on 98450 98450", name="Ravi Kumar", at=int(time.time()) + 3),
    )  # fmt: skip
    run_once()
    lead = _lead(PHONE)
    assert lead.is_human_takeover  # handed off -> an alert that names the customer exists
    sent = client.post(
        f"/api/leads/{lead.lead_id}/message", json={"text": "Calling you now, Ravi"}, headers=auth_headers
    ).json()
    assert sent["decision"] == "queued"  # still waiting in the outbox

    resp = client.delete(f"/api/leads/{lead.lead_id}/personal-data", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["events"] == 2 and body["cancelled"] == 1 and body["alerts"] >= 1

    with session_scope() as s:
        lead = s.get(Lead, lead.lead_id)
        assert (lead.name, lead.contact_phone, lead.conversation_summary) == (None, None, None)
        assert lead.erased_at and lead.opted_out and not lead.is_human_takeover
        assert lead.product_interest  # kept for analytics
        assert set(lead.qualify_slots) <= {"product", "category", "quantity", "timeline", "purchase_intent"}
        msgs = s.scalars(select(Message).where(Message.lead_id == lead.lead_id)).all()
        assert {m.text for m in msgs} == {"[erased]"} and all(m.platform_message_id is None for m in msgs)
        assert [m.delivery_status for m in msgs if m.error] == [DeliveryStatus.FAILED]
        raw = json.dumps([e.payload for e in s.scalars(select(InboundEvent)).all()], ensure_ascii=False)
        assert PHONE not in raw and "Ravi" not in raw
        assert OTHER in raw  # someone else's events are untouched
        for n in s.scalars(select(Notification).where(Notification.lead_id == lead.lead_id)):
            assert "Ravi" not in n.body + n.title and "98450" not in n.body
        assert s.scalar(select(LeadActivity.kind).where(LeadActivity.kind == "erase")) == "erase"

    assert client.delete(f"/api/leads/{lead.lead_id}/personal-data", headers=auth_headers).status_code == 409
    detail = client.get(f"/api/leads/{lead.lead_id}", headers=auth_headers).json()
    assert detail["erased_at"] and detail["reply_options"] == []

    run_once()  # the cancelled message is not sent
    with session_scope() as s:
        assert s.scalar(select(Message).where(Message.text == "Calling you now, Ravi")) is None

    # The same number writing again later is a new customer record.
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "hello", at=int(time.time()) + 6))
    run_once()
    assert _lead(PHONE).lead_id != lead.lead_id


def test_erase_is_per_business(client: TestClient, catalog, auth_headers) -> None:  # noqa: ANN001
    with session_scope() as s:
        other = create_user(s, "other@example.com", "another-password-123", "Other", "")
        lead = Lead(user_id=other.user_id, contact_phone="+919811111111", name="Not yours")
        s.add(lead)
        s.flush()
        lead_id = lead.lead_id
    assert client.delete(f"/api/leads/{lead_id}/personal-data", headers=auth_headers).status_code == 404
    with session_scope() as s:
        assert s.get(Lead, lead_id).name == "Not yours"


# -------------------------------------------------------------- analytics


def _msg(
    s, lead: Lead, at, direction: str, sender: str, cost: str = "0", category: str | None = None
) -> None:  # noqa: ANN001
    s.add(
        Message(
            user_id=lead.user_id, lead_id=lead.lead_id, channel=lead.preferred_channel or "whatsapp",
            direction=direction, sender=sender, text="x", timestamp=at, estimated_cost=Decimal(cost),
            pricing_category=category, delivery_status="sent" if direction == "outbound" else "received",
        )
    )  # fmt: skip


def _scenario(user_id: str) -> dict[str, str]:
    now = utcnow()
    with session_scope() as s:

        def lead(**kw) -> Lead:  # noqa: ANN003
            obj = Lead(user_id=user_id, **kw)
            s.add(obj)
            s.flush()
            return obj

        a = lead(source=LeadSource.CTWA_AD, preferred_channel="whatsapp", created_at=now - timedelta(hours=2),
                 qualification_score=85, status=LeadStatus.HOT)  # fmt: skip
        t = now - timedelta(hours=2)
        _msg(s, a, t, "inbound", Sender.LEAD)
        _msg(s, a, t + timedelta(seconds=3), "outbound", Sender.AGENT, "0", "free_entry")
        _msg(s, a, t + timedelta(seconds=60), "inbound", Sender.LEAD)

        b = lead(source=LeadSource.AD_FORM, preferred_channel="whatsapp", created_at=now - timedelta(days=3),
                 qualification_score=45, status=LeadStatus.CONVERTED)  # fmt: skip
        t = now - timedelta(days=3)
        _msg(s, b, t + timedelta(seconds=5), "outbound", Sender.AGENT, "0.8631", "marketing")
        _msg(s, b, t + timedelta(days=2), "outbound", Sender.NURTURE, "0.8631", "marketing")
        _msg(s, b, t + timedelta(days=2, hours=1), "inbound", Sender.LEAD)

        c = lead(source=LeadSource.COMMENT, preferred_channel="instagram", created_at=now - timedelta(hours=5),
                 qualification_score=10, status=LeadStatus.LOST)  # fmt: skip
        t = now - timedelta(hours=5)
        _msg(s, c, t, "inbound", Sender.LEAD)
        _msg(s, c, t + timedelta(seconds=120), "outbound", Sender.AGENT, "0", "free")
        s.add(ConsentEvent(user_id=user_id, lead_id=c.lead_id, type=ConsentType.OPT_OUT, channel="instagram"))
        s.add(Notification(user_id=user_id, lead_id=c.lead_id, kind=NotificationKind.HANDOFF, title="t",
                           body="b", idempotency_key="h1"))  # fmt: skip

        lead(
            source=LeadSource.DM, created_at=now - timedelta(days=40), status=LeadStatus.CONVERTED
        )  # outside
        return {"a": a.lead_id, "b": b.lead_id, "c": c.lead_id}


def test_summary(client: TestClient, user, auth_headers) -> None:  # noqa: ANN001
    """Lead-form lead B: first response is measured from the form, even though B wrote later (bug found here)."""
    ids = _scenario(user.user_id)
    assert (
        client.post(f"/api/leads/{ids['a']}/feedback", json={"agree": True}, headers=auth_headers).status_code
        == 200
    )
    client.post(f"/api/leads/{ids['c']}/feedback", json={"agree": True}, headers=auth_headers)
    client.post(
        f"/api/leads/{ids['c']}/feedback", json={"agree": False}, headers=auth_headers
    )  # changed mind

    r = client.get("/api/analytics/summary?days=30", headers=auth_headers).json()
    assert r["leads"]["total"] == 3
    assert r["leads"]["by_source"] == {"ctwa_ad": 1, "ad_form": 1, "comment": 1}
    assert [f["count"] for f in r["funnel"]] == [3, 2, 2, 1, 1]  # captured, replied, qualified, hot, bought
    assert r["rates"] == {
        "reply_rate_pct": 66.7, "qualified_pct": 66.7, "conversion_pct": 33.3, "win_rate_pct": 50.0,
    }  # fmt: skip
    fr = r["first_response"]
    assert (fr["measured"], fr["median_seconds"], fr["p95_seconds"], fr["within_target_pct"]) == (
        3,
        5.0,
        120.0,
        66.7,
    )
    assert (
        r["follow_ups"]["sent"] == 1
        and r["follow_ups"]["re_engaged"] == 1
        and r["follow_ups"]["opt_outs"] == 1
    )
    assert r["handoffs"] == 1
    assert r["hot_label_feedback"] == {"answered": 2, "agreed": 1, "agreement_pct": 50.0, "target_pct": 80}
    assert len(r["daily"]) == 30 and sum(d["leads"] for d in r["daily"]) == 3

    detail = client.get(f"/api/leads/{ids['c']}", headers=auth_headers).json()
    assert detail["hot_feedback"] is False


def test_costs(client: TestClient, user, auth_headers) -> None:  # noqa: ANN001
    _scenario(user.user_id)
    r = client.get("/api/analytics/costs?days=30", headers=auth_headers).json()
    assert Decimal(r["total"]) == Decimal("1.73") and r["messages"] == 4
    assert r["by_category"]["marketing"]["messages"] == 2
    assert Decimal(r["per_lead"]) == Decimal("0.58")
    assert Decimal(r["per_qualified_lead"]) == Decimal("0.86")
    assert Decimal(r["per_bought_lead"]) == Decimal("1.73")
    assert Decimal(r["saved_by_free_entry_window"]) == Decimal("0.12")
    assert abs(sum(Decimal(d["cost"]) for d in r["daily"]) - Decimal("1.73")) <= Decimal(
        "0.02"
    )  # per-day rounding


def test_empty_analytics(client: TestClient, user, auth_headers) -> None:  # noqa: ANN001
    r = client.get("/api/analytics/summary", headers=auth_headers).json()
    assert r["leads"]["total"] == 0 and r["first_response"]["median_seconds"] is None
    assert r["rates"]["conversion_pct"] is None
    assert client.get("/api/analytics/costs", headers=auth_headers).json()["per_lead"] is None
    assert client.get("/api/analytics/summary?days=0", headers=auth_headers).status_code == 422


def test_daily_buckets_use_business_time_zone(user) -> None:  # noqa: ANN001
    now = utcnow().replace(hour=20, minute=0, second=0, microsecond=0)  # 01:30 IST the next day
    with session_scope() as s:
        s.add(Lead(user_id=user.user_id, created_at=now - timedelta(minutes=10)))  # 01:20 IST
        s.flush()
        r = analytics.summary(s, user.user_id, 2, now=now)
    assert [d["leads"] for d in r["daily"]] == [0, 1]
