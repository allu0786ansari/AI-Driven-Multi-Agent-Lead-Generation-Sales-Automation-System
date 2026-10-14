"""Send Gate rules (spec Section 9) and outbox delivery."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.base import utcnow
from app.db.enums import Channel, DeliveryStatus, PricingCategory, Sender
from app.db.models import Lead, Message
from app.db.session import session_scope
from app.integrations.errors import ExternalAPIError
from app.integrations.factory import get_whatsapp_sender
from app.messaging.outbox import deliver_pending
from app.messaging.send_gate import Decision, OutboundContent, queue_message
from tests.test_guardrails import AF

TEXT = OutboundContent(text="Hello!")
TEMPLATE = OutboundContent(template_name="lead_form_welcome", template_params=["Priya", "air fryers"])


def _lead(user_id: str, **fields) -> str:  # noqa: ANN003
    now = utcnow()
    defaults = {
        "contact_phone": "+919876543210",
        "last_inbound_at": now,
        "service_window_expires_at": now + timedelta(hours=23),
    }
    with session_scope() as s:
        lead = Lead(user_id=user_id, qualify_slots={}, **{**defaults, **fields})
        s.add(lead)
        s.flush()
        return lead.lead_id


def _queue(lead_id: str, content: OutboundContent = TEXT, key: str = "k1", **kw):  # noqa: ANN003, ANN202
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        result = queue_message(s, lead, content, idempotency_key=key, **kw)
        cost = lead.messaging_cost_estimate
        msg = (result.message.pricing_category, result.message.estimated_cost) if result.message else None
    return result, msg, cost


def test_text_in_service_window_is_billed_as_service(user) -> None:  # noqa: ANN001
    result, msg, cost = _queue(_lead(user.user_id))
    assert result.decision == Decision.QUEUED
    assert msg == (PricingCategory.SERVICE, Decimal("0.1200"))
    assert cost == Decimal("0.1200")


def test_text_outside_window_needs_template(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id, service_window_expires_at=utcnow() - timedelta(minutes=1))
    result, _, _ = _queue(lead_id)
    assert result.decision == Decision.NEEDS_TEMPLATE


def test_ctwa_reply_within_24h_opens_free_entry_window(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id, ctwa_message_at=utcnow() - timedelta(hours=2))
    result, msg, cost = _queue(lead_id)
    assert msg == (PricingCategory.FREE_ENTRY, Decimal("0"))
    with session_scope() as s:
        window = s.get(Lead, lead_id).free_entry_window_expires_at
    assert timedelta(hours=71) < window - utcnow() <= timedelta(hours=72)
    # A marketing template inside the free window is also free.
    with session_scope() as s:
        s.get(Lead, lead_id).whatsapp_opt_in = True
    _, msg2, _ = _queue(lead_id, TEMPLATE, key="k2")
    assert msg2 == (PricingCategory.FREE_ENTRY, Decimal("0"))


def test_ctwa_reply_after_24h_is_not_free(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id, ctwa_message_at=utcnow() - timedelta(hours=25))
    _, msg, _ = _queue(lead_id)
    assert msg[0] == PricingCategory.SERVICE


def test_marketing_template_needs_opt_in(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id)
    result, _, _ = _queue(lead_id, TEMPLATE)
    assert (result.decision, result.reason) == (
        Decision.BLOCKED,
        "no WhatsApp opt-in for a marketing template",
    )
    with session_scope() as s:
        s.get(Lead, lead_id).whatsapp_opt_in = True
    result, msg, _ = _queue(lead_id, TEMPLATE)
    assert result.decision == Decision.QUEUED
    assert msg == (PricingCategory.MARKETING, Decimal("0.8631"))


def test_template_allowed_outside_window(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id, whatsapp_opt_in=True, service_window_expires_at=None)
    assert _queue(lead_id, TEMPLATE)[0].decision == Decision.QUEUED


def test_opted_out_blocked_except_confirmation(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id, opted_out=True)
    assert _queue(lead_id)[0].reason == "lead opted out"
    assert _queue(lead_id, allow_opted_out=True)[0].decision == Decision.QUEUED


def test_takeover_blocks_agent_not_salesperson(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id, is_human_takeover=True)
    assert _queue(lead_id)[0].reason == "human takeover active"
    assert _queue(lead_id, sender=Sender.SALESPERSON)[0].decision == Decision.QUEUED


@pytest.mark.parametrize(
    ("fields", "kwargs", "reason"),
    [
        ({"contact_phone": None}, {}, "lead has no WhatsApp number"),
        ({}, {"channel": Channel.INSTAGRAM}, "channel instagram not supported yet"),
    ],
)
def test_other_blocks(user, fields, kwargs, reason) -> None:  # noqa: ANN001
    assert _queue(_lead(user.user_id, **fields), **kwargs)[0].reason == reason


def test_guardrail_blocks_wrong_price(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id)
    bad = OutboundContent(text="The air fryer is ₹3,999!")
    result, _, _ = _queue(lead_id, bad, context_products=[AF])
    assert result.decision == Decision.BLOCKED and "guardrail" in result.reason
    good = OutboundContent(text="The air fryer is ₹4,999!")
    assert _queue(lead_id, good, key="k2", context_products=[AF])[0].decision == Decision.QUEUED


def test_idempotent(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id)
    first = _queue(lead_id)[0]
    second = _queue(lead_id)[0]
    assert second.reason == "already queued (idempotent)"
    assert first.message.message_id == second.message.message_id
    with session_scope() as s:
        assert len(s.scalars(select(Message)).all()) == 1
        assert s.get(Lead, lead_id).messaging_cost_estimate == Decimal("0.1200")  # charged once


def test_content_must_be_text_xor_template(user) -> None:  # noqa: ANN001
    with pytest.raises(ValueError):
        _queue(_lead(user.user_id), OutboundContent())


# ------------------------------------------------------------------- outbox


def _messages() -> list[Message]:
    with session_scope() as s:
        return list(s.scalars(select(Message).order_by(Message.timestamp)))


def test_outbox_sends_text_and_template(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id, whatsapp_opt_in=True)
    _queue(lead_id)
    _queue(lead_id, TEMPLATE, key="k2")
    assert deliver_pending() == {"sent": 2}
    sender = get_whatsapp_sender()
    assert [m["type"] for m in sender.sent] == ["text", "template"]
    assert sender.sent[0] == {**sender.sent[0], "to": "919876543210", "body": "Hello!"}
    assert sender.sent[1]["params"] == ["Priya", "air fryers"] and sender.sent[1]["language"] == "en"
    msgs = _messages()
    assert all(m.delivery_status == DeliveryStatus.SENT and m.platform_message_id and m.sent_at for m in msgs)
    assert deliver_pending() == {}


def test_outbox_retries_transient_then_succeeds(user) -> None:  # noqa: ANN001
    _queue(_lead(user.user_id))
    get_whatsapp_sender().fail_next.append(ExternalAPIError("503", transient=True))
    assert deliver_pending() == {"queued": 1}
    [m] = _messages()
    assert m.attempts == 1 and m.next_attempt_at > utcnow() + timedelta(seconds=9)
    assert deliver_pending() == {}  # backoff not over
    with session_scope() as s:
        s.get(Message, m.message_id).next_attempt_at = utcnow()
    assert deliver_pending() == {"sent": 1}


def test_outbox_permanent_error_fails(user) -> None:  # noqa: ANN001
    _queue(_lead(user.user_id))
    get_whatsapp_sender().fail_next.append(ExternalAPIError("re-engagement", transient=False, code=131047))
    assert deliver_pending() == {"failed": 1}
    assert "re-engagement" in _messages()[0].error


def test_outbox_gives_up_after_max_attempts(user) -> None:  # noqa: ANN001
    _queue(_lead(user.user_id))
    for _ in range(3):
        get_whatsapp_sender().fail_next.append(ExternalAPIError("timeout", transient=True))
        with session_scope() as s:
            for m in s.scalars(select(Message)):
                m.next_attempt_at = utcnow()
        deliver_pending()
    assert _messages()[0].delivery_status == DeliveryStatus.FAILED
    assert _messages()[0].attempts == 3


def test_outbox_never_resends_stuck_message(user) -> None:  # noqa: ANN001
    _queue(_lead(user.user_id))
    with session_scope() as s:
        m = s.scalar(select(Message))
        m.delivery_status = DeliveryStatus.SENDING
        m.next_attempt_at = utcnow() - timedelta(minutes=11)
    assert deliver_pending() == {"failed_stuck": 1}
    assert get_whatsapp_sender().sent == []
    assert "outcome unknown" in _messages()[0].error
