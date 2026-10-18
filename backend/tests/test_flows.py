"""End-to-end Week 2 flows: signed webhook in -> worker -> agents -> Send Gate -> outbox -> fake WhatsApp."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.enums import DeliveryStatus, EventStatus
from app.db.models import InboundEvent, Lead, Message
from app.db.session import session_scope
from app.dev import payloads as p
from app.integrations.errors import ExternalAPIError
from app.integrations.factory import get_meta_graph, get_whatsapp_sender
from app.llm.base import LLMError
from app.llm.fake import FakeLLMClient
from app.worker.processor import run_once
from tests.conftest import signed_post

PHONE = "919876543210"


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLMClient:
    fake = FakeLLMClient()
    monkeypatch.setattr("app.agents.responder.get_llm_client", lambda: fake)
    return fake


def _draft(reply: str, needs_human: bool = False, skus: list[str] | None = None) -> dict:
    return {"reply": reply, "language": "en", "needs_human": needs_human, "product_skus": skus or []}


def _lead(phone: str = PHONE) -> Lead:
    with session_scope() as s:
        return s.scalar(select(Lead).where(Lead.contact_phone == "+" + phone))


def _outbound(lead_id: str) -> list[Message]:
    with session_scope() as s:
        return list(
            s.scalars(
                select(Message)
                .where(Message.lead_id == lead_id, Message.direction == "outbound")
                .order_by(Message.timestamp)
            )
        )


def _events() -> list[InboundEvent]:
    with session_scope() as s:
        return list(s.scalars(select(InboundEvent).order_by(InboundEvent.received_at)))


def test_ctwa_message_gets_grounded_free_reply(client: TestClient, catalog, llm: FakeLLMClient) -> None:  # noqa: ANN001
    llm.enqueue(
        _draft(
            "Our {{name:AF-6L-WIN}} is {{price:AF-6L-WIN}} and {{stock:AF-6L-WIN}}. Which city should we deliver to?"
        )
    )
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_ctwa(PHONE, "6 litre air fryer price?", ad_id="AD1"))
    counts = run_once()
    assert counts == {"event_processed": 1, "message_sent": 1}

    lead = _lead()
    assert (lead.source, lead.ad_id, lead.name) == ("ctwa_ad", "AD1", "Ad Customer")
    [reply] = _outbound(lead.lead_id)
    assert (
        reply.text
        == "Our CrispAir 6L Air Fryer with Window is ₹6,799 and in stock. Which city should we deliver to?"
    )
    assert (reply.pricing_category, reply.estimated_cost) == ("free_entry", Decimal("0"))
    assert reply.delivery_status == DeliveryStatus.SENT
    assert get_whatsapp_sender().sent[0]["body"] == reply.text
    assert lead.free_entry_window_expires_at is not None

    # The LLM saw the catalog (with SKUs) and the customer's message inside delimiters.
    prompt = llm.calls[0]["messages"][1].content
    assert (
        "SKU AF-6L-WIN" in prompt
        and "<customer_message>\n6 litre air fryer price?\n</customer_message>" in prompt
    )

    # Delivery receipts move the message forward; a late "sent" never moves it back.
    for state in ("delivered", "read", "sent"):
        signed_post(client, "/webhooks/whatsapp", p.whatsapp_status(reply.platform_message_id, state))
    run_once()
    assert _outbound(lead.lead_id)[0].delivery_status == DeliveryStatus.READ


def test_hallucinated_price_replaced_by_catalog_reply(
    client: TestClient, catalog, llm: FakeLLMClient
) -> None:  # noqa: ANN001
    llm.enqueue(_draft("The air fryer is just ₹2,999 with free delivery!"))
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "air fryer price?"))
    run_once()
    [reply] = _outbound(_lead().lead_id)
    assert "2,999" not in reply.text and "free delivery" not in reply.text
    assert reply.text.startswith("Here are the options that match:")
    assert "₹4,999" in reply.text and "₹6,799" in reply.text
    [ev] = _events()
    assert "reply fallback" in ev.result_note and "not a catalog price" in ev.result_note


def test_llm_down_uses_catalog_fallback(client: TestClient, catalog, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    def boom(messages, schema):  # noqa: ANN001, ANN202
        raise LLMError("Ollama not reachable")

    monkeypatch.setattr("app.agents.responder.get_llm_client", lambda: FakeLLMClient(responder=boom))
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "mixer grinder price"))
    run_once()
    [reply] = _outbound(_lead().lead_id)
    assert "PowerChef" in reply.text
    assert not _lead().is_human_takeover


def test_no_matching_product_and_llm_down_hands_off(
    client: TestClient, user, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "app.agents.responder.get_llm_client",
        lambda: FakeLLMClient(responder=lambda m, s: (_ for _ in ()).throw(LLMError("x"))),
    )
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "hello"))  # no catalog loaded
    run_once()
    lead = _lead()
    [reply] = _outbound(lead.lead_id)
    assert "team will reply" in reply.text
    assert reply.sender == "system"
    assert lead.is_human_takeover


def test_needs_human_hands_off_and_silences_agent(client: TestClient, catalog, llm: FakeLLMClient) -> None:  # noqa: ANN001
    llm.enqueue(_draft("Sorry about that! A team member will reply soon.", needs_human=True))
    signed_post(
        client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "my order arrived broken, I want a refund")
    )
    run_once()
    assert _lead().is_human_takeover
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "hello??"))
    run_once()
    assert len(_outbound(_lead().lead_id)) == 1
    assert "human takeover" in _events()[-1].result_note


def test_voice_note_handed_off(client: TestClient, catalog, llm: FakeLLMClient) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_audio(PHONE))
    run_once()
    lead = _lead()
    [reply] = _outbound(lead.lead_id)
    assert reply.text.startswith("Thanks for the voice note!")
    assert lead.is_human_takeover
    assert llm.calls == []


def test_stop_opts_out_and_confirms_once(client: TestClient, catalog, llm: FakeLLMClient) -> None:  # noqa: ANN001
    import time

    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "STOP"))
    run_once()
    lead = _lead()
    assert lead.opted_out
    [confirmation] = _outbound(lead.lead_id)
    assert "won't receive any more offers or reminders" in confirmation.text
    assert llm.calls == []
    # Week 5 (D-030): a later question from the customer is answered; follow-ups stay off.
    signed_post(
        client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "air fryer price", at=int(time.time()) + 5)
    )
    run_once()
    assert len(_outbound(lead.lead_id)) == 2
    assert _lead().opted_out


def test_duplicate_webhook_replies_once(client: TestClient, catalog, llm: FakeLLMClient) -> None:  # noqa: ANN001
    payload = p.whatsapp_text(PHONE, "kettle price?", msg_id="wamid.DUPX")
    signed_post(client, "/webhooks/whatsapp", payload)
    signed_post(client, "/webhooks/whatsapp", payload)
    run_once()
    assert len(_outbound(_lead().lead_id)) == 1


def test_handler_crash_after_queueing_does_not_double_send(
    client: TestClient, catalog, llm: FakeLLMClient, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """The reply is queued, then the handler fails: the queued row is rolled back, retried once, sent once."""
    import app.handlers.whatsapp as wa

    real = wa.respond_to_whatsapp
    calls = {"n": 0}

    def flaky(session, capture):  # noqa: ANN001, ANN202
        result = real(session, capture)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash after queueing")
        return result

    monkeypatch.setattr(wa, "respond_to_whatsapp", flaky)
    llm.enqueue(
        _draft("{{name:EK-1.8-SS}} is {{price:EK-1.8-SS}}."),
        _draft("{{name:EK-1.8-SS}} is {{price:EK-1.8-SS}}."),
    )
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "kettle?"))
    assert run_once() == {"event_retry": 1}
    assert get_whatsapp_sender().sent == []
    with session_scope() as s:
        s.scalar(select(InboundEvent)).next_attempt_at = s.scalar(select(InboundEvent)).received_at
    assert run_once() == {"event_processed": 1, "message_sent": 1}
    assert len(get_whatsapp_sender().sent) == 1
    with session_scope() as s:
        assert len(s.scalars(select(Lead)).all()) == 1


def test_lead_form_with_opt_in_sends_welcome_template(client: TestClient, user) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/meta/page", p.leadgen(leadgen_id="55551234"))
    assert run_once() == {"event_processed": 1, "message_sent": 1}
    assert get_meta_graph().calls == ["55551234"]
    [sent] = get_whatsapp_sender().sent
    assert sent == {
        **sent,
        "type": "template",
        "name": "lead_form_welcome",
        "params": ["Form", "Air Fryers"],
        "to": "919855551234",
    }
    lead = _lead("919855551234")
    [msg] = _outbound(lead.lead_id)
    assert (msg.pricing_category, msg.estimated_cost) == ("marketing", Decimal("0.8631"))
    assert lead.messaging_cost_estimate == Decimal("0.8631")

    # Found in the Week 2 sandbox run: a receipt without billing info must not relabel the category.
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_status(msg.platform_message_id, "delivered"))
    run_once()
    [msg] = _outbound(lead.lead_id)
    assert (msg.delivery_status, msg.pricing_category) == ("delivered", "marketing")


def test_lead_form_without_opt_in_sends_nothing(client: TestClient, user) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/meta/page", p.leadgen(leadgen_id="0009999"))
    assert run_once() == {"event_processed": 1}
    assert get_whatsapp_sender().sent == []
    assert "no WhatsApp opt-in" in _events()[0].result_note


def test_lead_form_fetch_error_is_retried(client: TestClient, user, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    graph = get_meta_graph()
    real = graph.fetch_lead
    attempts = {"n": 0}

    def flaky(leadgen_id: str):  # noqa: ANN202
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ExternalAPIError("Graph 500", transient=True)
        return real(leadgen_id)

    monkeypatch.setattr(graph, "fetch_lead", flaky)
    signed_post(client, "/webhooks/meta/page", p.leadgen(leadgen_id="4444"))
    assert run_once() == {"event_retry": 1}
    with session_scope() as s:
        ev = s.scalar(select(InboundEvent))
        ev.next_attempt_at = ev.received_at
    assert run_once() == {"event_processed": 1, "message_sent": 1}


def test_status_for_unknown_message_retried_then_ignored(client: TestClient, user) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_status("wamid.NOT-OURS", "delivered"))
    for _ in range(3):
        run_once()
        with session_scope() as s:
            ev = s.scalar(select(InboundEvent))
            ev.next_attempt_at = ev.received_at
    [ev] = _events()
    assert (ev.status, ev.attempts) == (EventStatus.IGNORED, 3)


def test_failed_status_and_billing_correction(client: TestClient, catalog, llm: FakeLLMClient) -> None:  # noqa: ANN001
    llm.enqueue(
        _draft("{{name:EK-1.8-SS}}: {{price:EK-1.8-SS}}"), _draft("{{name:EK-1.8-SS}}: {{price:EK-1.8-SS}}")
    )
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "kettle"))
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "kettle again"))
    run_once()
    first, second = _outbound(_lead().lead_id)
    assert _lead().messaging_cost_estimate == Decimal("0.24")
    free = p.whatsapp_status(first.platform_message_id, "delivered")
    free["entry"][0]["changes"][0]["value"]["statuses"][0]["pricing"] = {
        "billable": False,
        "type": "free_customer_service",
        "category": "service",
    }
    signed_post(client, "/webhooks/whatsapp", free)
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_status(second.platform_message_id, "failed"))
    run_once()
    first, second = _outbound(_lead().lead_id)
    assert first.estimated_cost == 0 and _lead().messaging_cost_estimate == Decimal("0.12")
    assert second.delivery_status == "failed" and second.error.startswith("131047")


def test_lead_api_and_product_search(
    client: TestClient, catalog, auth_headers: dict, llm: FakeLLMClient
) -> None:  # noqa: ANN001
    llm.enqueue(_draft("{{name:IC-2000-TCH}} costs {{price:IC-2000-TCH}}."))
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "induction price"))
    run_once()
    leads = client.get("/api/leads", headers=auth_headers).json()
    assert len(leads) == 1 and leads[0]["contact_phone"] == "+" + PHONE
    detail = client.get(f"/api/leads/{leads[0]['lead_id']}", headers=auth_headers).json()
    assert [m["direction"] for m in detail["messages"]] == ["inbound", "outbound"]
    assert detail["messages"][1]["text"] == "FlameFree 2000W Induction Cooktop costs ₹2,699."
    assert client.get("/api/leads/nope", headers=auth_headers).status_code == 404

    search = client.get(
        "/api/products/search", params={"q": "air fryer under 6000"}, headers=auth_headers
    ).json()
    assert search["filters"]["max_price"] == "6000"
    assert search["results"][0]["sku"] == "AF-4L-DIG"
    assert all(Decimal(r["price"]) <= 6000 for r in search["results"])
