"""End-to-end Week 3 behaviour on WhatsApp: qualification, next question, hot alert, human requests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Lead, Message, Notification
from app.db.session import session_scope
from app.dev import payloads as p
from app.integrations.factory import get_whatsapp_sender
from app.llm.fake import FakeLLMClient
from app.worker.processor import run_once
from tests.conftest import signed_post

PHONE = "919811100000"


def _lead() -> Lead:
    with session_scope() as s:
        return s.scalar(select(Lead))


def _notes() -> list[Notification]:
    with session_scope() as s:
        return list(s.scalars(select(Notification).order_by(Notification.created_at)))


def test_conversation_becomes_hot_and_alerts_once(client: TestClient, catalog, auth_headers: dict) -> None:  # noqa: ANN001
    signed_post(
        client,
        "/webhooks/whatsapp",
        p.whatsapp_text(PHONE, "Hi, I want the 6 litre air fryer", name="Hot Lead"),
    )
    run_once()
    first = get_whatsapp_sender().sent[0]["body"]
    # Fallback reply (no LLM in tests) ends with the next qualifying question instead of a generic one.
    assert first.endswith("How many would you need?")
    lead = _lead()
    assert lead.status in ("cold", "warm") and lead.qualify_state == "in_progress"

    signed_post(
        client,
        "/webhooks/whatsapp",
        p.whatsapp_text(PHONE, "Need 2 pcs, budget 15000, delivery to Chennai this week. Want to order"),
    )
    counts = run_once()
    assert counts["alert_logged"] == 1
    lead = _lead()
    assert lead.status == "hot" and lead.qualification_score >= 70
    assert lead.conversation_summary.startswith(
        "Wants: CrispAir 6L Air Fryer with Window · Qty 2 · Budget ₹15,000"
    )
    [hot] = _notes()
    assert hot.kind == "hot_lead" and "Hot Lead" in hot.title

    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "ok thanks"))
    run_once()
    assert len(_notes()) == 1  # still hot: no second alert

    api = client.get("/api/notifications", headers=auth_headers).json()
    assert api[0]["kind"] == "hot_lead" and api[0]["status"] == "logged"
    detail = client.get(f"/api/leads/{lead.lead_id}", headers=auth_headers).json()
    assert (
        detail["qualify_slots"]["city"] == "Chennai"
        and "budget covers the product" in detail["score_rationale"]
    )


def test_llm_extracted_details_are_used(client: TestClient, catalog, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    fake = FakeLLMClient()
    monkeypatch.setattr("app.agents.responder.get_llm_client", lambda: fake)
    fake.enqueue(
        {
            "reply": "{{name:EK-1.8-SS}} is {{price:EK-1.8-SS}}. Which city should we deliver to?",
            "language": "hinglish",
            "needs_human": False,
            "product_skus": ["EK-1.8-SS"],
            "extracted": {
                "product": "QuickBoil 1.8L Steel Electric Kettle",
                "quantity": 4,
                "timeline": "this_week",
            },
        }
    )
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "chaar kettle chahiye is hafte"))
    run_once()
    slots = _lead().qualify_slots
    assert (slots["product"], slots["quantity"], slots["timeline"]) == (
        "QuickBoil 1.8L Steel Electric Kettle",
        4,
        "this_week",
    )
    # The prompt asked the model to end with the next missing question (after product/quantity/timeline: city).
    prompt = fake.calls[0]["messages"][1].content
    assert "NEXT QUESTION: How many would you need?" in prompt  # rules alone did not see "chaar" (four)
    assert "extracted" in fake.calls[0]["json_schema"]["properties"]


def test_asking_for_a_person_hands_off(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "Can someone call me please?"))
    run_once()
    lead = _lead()
    assert lead.is_human_takeover
    assert get_whatsapp_sender().sent[0]["body"] == "Sure! A member of our team will contact you shortly."
    [n] = _notes()
    assert n.kind == "handoff" and "asked to talk to a person" in n.body

    # While a person handles the lead, new messages alert the salesperson instead of the AI replying.
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "hello? anyone?"))
    run_once()
    assert len(get_whatsapp_sender().sent) == 1
    assert [n.kind for n in _notes()] == ["handoff", "customer_replied"]


def test_handlers_survive_missing_catalog(client: TestClient, user) -> None:  # noqa: ANN001
    """No products, no LLM: holding reply + handoff + alert, nothing crashes."""
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "hello"))
    run_once()
    assert _lead().is_human_takeover
    assert [n.kind for n in _notes()] == ["handoff"]
    with session_scope() as s:
        assert s.scalar(select(Message).where(Message.direction == "outbound")).sender == "system"
