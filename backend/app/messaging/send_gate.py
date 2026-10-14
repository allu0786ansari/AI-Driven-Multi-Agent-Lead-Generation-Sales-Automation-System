"""Send Gate (proposal Section 3, spec Section 9): the only way to send a message.

Every outbound message is decided here, in this order:

  1. Idempotency    - the same key never creates a second message
  2. Opt-out        - nothing goes to a lead who opted out (except the opt-out confirmation)
  3. Human takeover - agents stay silent while a salesperson has the conversation
  4. Channel        - WhatsApp now; Instagram/Messenger arrive in Week 3
  5. Window rules   - free-form text only inside the 24h service window,
                      otherwise a pre-approved template is required
  6. Consent        - marketing templates need recorded WhatsApp opt-in
  7. Guardrails     - agent text must pass the price/claim checks
  8. Cost           - free inside the 72h free entry window (opened by replying
                      within 24h to a Click-to-WhatsApp message), otherwise
                      estimated from the rate card

Allowed messages are written to the outbox as `queued`, in the caller's
transaction; app/messaging/outbox.py sends them after that transaction commits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.guardrails import validate_reply
from app.config import get_settings
from app.db.base import utcnow
from app.db.enums import Channel, DeliveryStatus, Direction, PricingCategory, Sender
from app.db.models import Lead, Message
from app.rag.records import ProductRecord

log = logging.getLogger(__name__)

FREE_ENTRY_WINDOW = timedelta(hours=72)
CTWA_REPLY_DEADLINE = timedelta(hours=24)


class Decision(StrEnum):
    QUEUED = "queued"
    NEEDS_TEMPLATE = "needs_template"  # free-form not allowed now; caller must use a template
    BLOCKED = "blocked"


@dataclass(frozen=True)
class OutboundContent:
    text: str | None = None
    template_name: str | None = None
    template_params: list[str] = field(default_factory=list)
    template_category: PricingCategory = PricingCategory.MARKETING
    language: str | None = None

    @property
    def is_template(self) -> bool:
        return self.template_name is not None


@dataclass
class SendResult:
    decision: Decision
    reason: str = ""
    message: Message | None = None


def _rate(category: PricingCategory) -> Decimal:
    s = get_settings()
    rates = {
        PricingCategory.MARKETING: s.wa_rate_marketing_inr,
        PricingCategory.UTILITY: s.wa_rate_utility_inr,
        PricingCategory.SERVICE: s.wa_rate_service_inr,
    }
    return Decimal(str(rates.get(category, 0)))


def _open(until: datetime | None, now: datetime) -> bool:
    return until is not None and until > now


def queue_message(
    session: Session,
    lead: Lead,
    content: OutboundContent,
    *,
    idempotency_key: str,
    sender: Sender = Sender.AGENT,
    channel: Channel = Channel.WHATSAPP,
    context_products: list[ProductRecord] | None = None,
    customer_text: str = "",
    allow_opted_out: bool = False,
    now: datetime | None = None,
) -> SendResult:
    now = now or utcnow()

    existing = session.scalar(select(Message).where(Message.idempotency_key == idempotency_key))
    if existing is not None:
        return SendResult(Decision.QUEUED, "already queued (idempotent)", existing)

    def blocked(reason: str) -> SendResult:
        log.info("send blocked", extra={"reason": reason, "lead_id": lead.lead_id})
        return SendResult(Decision.BLOCKED, reason)

    if lead.opted_out and not allow_opted_out:
        return blocked("lead opted out")
    if sender == Sender.AGENT and lead.is_human_takeover:
        return blocked("human takeover active")
    if channel != Channel.WHATSAPP:
        return blocked(f"channel {channel} not supported yet")
    if not lead.contact_phone:
        return blocked("lead has no WhatsApp number")
    if (content.text is None) == (content.template_name is None):
        raise ValueError("OutboundContent needs exactly one of text or template_name")

    free_entry = _open(lead.free_entry_window_expires_at, now)
    opens_free_entry = (
        not free_entry
        and lead.ctwa_message_at is not None
        and now - lead.ctwa_message_at <= CTWA_REPLY_DEADLINE
        and lead.free_entry_window_expires_at is None
    )

    if content.is_template:
        if content.template_category == PricingCategory.MARKETING and not lead.whatsapp_opt_in:
            return blocked("no WhatsApp opt-in for a marketing template")
        category = PricingCategory.FREE_ENTRY if free_entry else content.template_category
        payload = {
            "type": "template",
            "name": content.template_name,
            "language": content.language or get_settings().template_language,
            "params": list(content.template_params),
        }
        preview = f"[template {content.template_name}] " + " | ".join(content.template_params)
    else:
        if not _open(lead.service_window_expires_at, now):
            return SendResult(Decision.NEEDS_TEMPLATE, "24-hour service window is closed")
        if sender == Sender.AGENT and context_products is not None:
            check = validate_reply(content.text or "", context_products, customer_text)
            if not check.ok:
                return blocked("guardrail: " + "; ".join(check.problems))
        if opens_free_entry:
            lead.free_entry_window_expires_at = now + FREE_ENTRY_WINDOW
            free_entry = True
        category = PricingCategory.FREE_ENTRY if free_entry else PricingCategory.SERVICE
        payload = {"type": "text"}
        preview = content.text

    cost = Decimal("0") if category == PricingCategory.FREE_ENTRY else _rate(category)
    message = Message(
        user_id=lead.user_id,
        lead_id=lead.lead_id,
        channel=channel,
        direction=Direction.OUTBOUND,
        sender=sender,
        text=preview,
        template_name=content.template_name,
        pricing_category=category,
        estimated_cost=cost,
        delivery_status=DeliveryStatus.QUEUED,
        idempotency_key=idempotency_key,
        payload=payload,
        timestamp=now,
        next_attempt_at=now,
    )
    session.add(message)
    lead.messaging_cost_estimate = Decimal(str(lead.messaging_cost_estimate or 0)) + cost
    lead.last_activity_at = now
    session.flush()
    return SendResult(Decision.QUEUED, "", message)
