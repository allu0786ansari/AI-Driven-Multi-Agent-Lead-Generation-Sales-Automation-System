"""Week 6 failure drills (spec Section 5): what the customer and the salesperson see when things break.

Each drill sets up the failure end to end (webhook -> worker -> outbox) and
checks two things: the customer is never left without an answer or sent
something twice, and the salesperson finds out when a person is needed.
Drills already covered elsewhere are listed in docs/RUNLOG.md (Week 6).
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.db.base import utcnow
from app.db.models import InboundEvent, Lead, Message, Notification
from app.db.session import session_scope
from app.dev import payloads as p
from app.integrations.errors import ExternalAPIError
from app.integrations.factory import get_whatsapp_sender
from app.llm.base import LLMError
from app.messaging.send_gate import Decision, OutboundContent, queue_message
from app.worker.processor import run_once
from tests.conftest import signed_post

PHONE = "919811077777"


def _lead() -> Lead:
    with session_scope() as s:
        return s.scalar(select(Lead).where(Lead.contact_phone == "+" + PHONE))


def _outbound() -> list[Message]:
    with session_scope() as s:
        return list(
            s.scalars(
                select(Message)
                .join(Lead, Lead.lead_id == Message.lead_id)
                .where(Lead.contact_phone == "+" + PHONE, Message.direction == "outbound")
                .order_by(Message.timestamp)
            )
        )


def _alerts(kind: str) -> list[Notification]:
    with session_scope() as s:
        return list(s.scalars(select(Notification).where(Notification.kind == kind)))


def test_drill_meta_delivers_the_same_webhook_three_times(client: TestClient, catalog) -> None:  # noqa: ANN001
    payload = p.whatsapp_text(PHONE, "kettle price?", msg_id="wamid.DRILL1")
    for _ in range(3):
        assert signed_post(client, "/webhooks/whatsapp", payload).status_code == 200
    run_once()
    run_once()
    assert len(_outbound()) == 1 and len(get_whatsapp_sender().sent) == 1
    with session_scope() as s:
        assert len(s.scalars(select(Lead)).all()) == 1


def test_drill_model_down(client: TestClient, catalog, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """Ollama stopped: the customer still gets correct prices within the same pass, nobody is paged."""

    class Down:
        is_scripted = True

        def chat(self, *a, **kw):  # noqa: ANN002, ANN003, ANN202
            raise LLMError("connection refused: http://localhost:11434")

        def health(self) -> bool:
            return False

    monkeypatch.setattr("app.agents.responder.get_llm_client", lambda: Down())
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "6L air fryer price?"))
    run_once()
    [reply] = _outbound()
    assert "₹6,799" in reply.text and reply.delivery_status == "sent"
    assert not _lead().is_human_takeover and _alerts("handoff") == []


def test_drill_whatsapp_api_hiccup_is_retried(client: TestClient, catalog) -> None:  # noqa: ANN001
    get_whatsapp_sender().fail_next.append(ExternalAPIError("503 from Meta", transient=True, code=503))
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "kettle price?"))
    run_once()
    [msg] = _outbound()
    assert msg.delivery_status == "queued" and msg.attempts == 1  # waiting for the retry
    with session_scope() as s:
        s.execute(update(Message).values(next_attempt_at=utcnow()))  # the backoff has passed
    run_once()
    [msg] = _outbound()
    assert msg.delivery_status == "sent" and len(get_whatsapp_sender().sent) == 1
    assert _alerts("message_failed") == []


def test_drill_message_rejected_alerts_the_salesperson(client: TestClient, catalog, auth_headers) -> None:  # noqa: ANN001
    get_whatsapp_sender().fail_next.append(
        ExternalAPIError("131026: message undeliverable", transient=False, code=131026)
    )
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "kettle price?"))
    run_once()
    [msg] = _outbound()
    assert msg.delivery_status == "failed"
    [alert] = _alerts("message_failed")
    assert "could not be delivered: 131026" in alert.body and "Check the conversation" in alert.body
    listed = client.get("/api/notifications", headers=auth_headers).json()
    assert any(n["kind"] == "message_failed" for n in listed)
    run_once()
    assert len(_alerts("message_failed")) == 1  # never repeated


def test_drill_meta_reports_failure_later(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "kettle price?"))
    run_once()
    [msg] = _outbound()
    failed = p.whatsapp_status(msg.platform_message_id, "failed", recipient=PHONE)
    for _ in range(2):  # Meta may send the same status twice
        signed_post(client, "/webhooks/whatsapp", failed)
    run_once()
    assert _outbound()[0].delivery_status == "failed"
    assert len(_alerts("message_failed")) == 1


def test_drill_server_killed_while_sending(client: TestClient, catalog) -> None:  # noqa: ANN001
    """The server died during the API call: never re-send (the customer may have it), tell a person."""
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "kettle price?"))
    from app.worker.processor import process_pending

    process_pending()  # reply queued, not yet sent
    with session_scope() as s:
        s.execute(
            update(Message)
            .where(Message.direction == "outbound")
            .values(delivery_status="sending", next_attempt_at=utcnow() - timedelta(minutes=11))
        )
    run_once()
    [msg] = _outbound()
    assert msg.delivery_status == "failed" and "outcome unknown" in msg.error
    assert get_whatsapp_sender().sent == []
    [alert] = _alerts("message_failed")
    assert "outcome unknown" in alert.body


def test_drill_server_killed_while_handling_an_event(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "kettle price?"))
    with session_scope() as s:  # claimed by a worker that then died
        s.execute(
            update(InboundEvent).values(status="processing", next_attempt_at=utcnow() - timedelta(minutes=6))
        )
    run_once()
    assert len(_outbound()) == 1 and _outbound()[0].delivery_status == "sent"


def test_drill_window_closed(client: TestClient, catalog, auth_headers) -> None:  # noqa: ANN001
    yesterday = int(time.time()) - 25 * 3600
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "kettle price?", at=yesterday))
    run_once()
    lead = _lead()
    r = client.post(
        f"/api/leads/{lead.lead_id}/message", json={"text": "Still interested?"}, headers=auth_headers
    )
    assert r.json()["decision"] == "needs_template"
    with session_scope() as s:  # with opt-in, an approved template still works
        s.get(Lead, lead.lead_id).whatsapp_opt_in = True
    r = client.post(
        f"/api/leads/{lead.lead_id}/message",
        json={"template": {"name": "nurture_followup_1", "params": ["Ravi", "Kettles"]}},
        headers=auth_headers,
    )
    assert r.json()["decision"] == "queued"


def test_drill_opted_out_customer(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "band karo"))
    run_once()
    with session_scope() as s:
        lead = s.scalar(select(Lead).where(Lead.contact_phone == "+" + PHONE))
        lead.whatsapp_opt_in = True
        r = queue_message(
            s, lead, OutboundContent(template_name="nurture_offer"), idempotency_key="drill-tpl"
        )
    assert r.decision == Decision.BLOCKED
    assert len(_outbound()) == 1  # only the opt-out confirmation


def test_drill_forged_webhook(client: TestClient, catalog) -> None:  # noqa: ANN001
    r = signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "hi"), secret="not-the-app-secret")
    assert r.status_code == 401  # rejected before anything is stored
    with session_scope() as s:
        assert s.scalars(select(InboundEvent)).all() == []
