"""Send Gate (proposal Section 3, spec Section 9): the only way to send a message.

Every outbound message is decided here, in this order:

  1. Idempotency    - the same key never creates a second message
  2. Opt-out        - nothing the business starts goes to a lead who opted out
                      (templates, follow-ups). If the customer writes again
                      afterwards, normal replies to that conversation are
                      allowed (Week 5, D-030). The opt-out confirmation itself
                      is always allowed.
  3. Human takeover - the assistant and follow-ups stay silent while a
                      salesperson has the conversation
  4. Channel        - WhatsApp, Instagram, Messenger (Week 3)
  5. Window rules   - WhatsApp: free-form text only inside the 24h service
                      window, otherwise a pre-approved template is required.
                      Instagram/Messenger: DMs only within 24h of the lead's
                      last message on that channel; one private reply per
                      comment within 7 days of the comment; no templates.
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
    # Instagram / Facebook comments (Week 3). `text` holds the message.
    private_reply_to: str | None = None  # comment ID: one private message to the commenter
    public_reply_to: str | None = None  # comment ID: visible reply under the comment
    comment_created_at: datetime | None = None

    @property
    def is_template(self) -> bool:
        return self.template_name is not None


@dataclass
class SendResult:
    decision: Decision
    reason: str = ""
    message: Message | None = None


def wrote_after_opt_out(lead: Lead) -> bool:
    """The customer messaged us after opting out, so replying to them is allowed."""
    return (
        lead.opted_out_at is not None
        and lead.last_inbound_at is not None
        and lead.last_inbound_at > lead.opted_out_at
    )


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
        if content.is_template or sender == Sender.NURTURE or not wrote_after_opt_out(lead):
            return blocked("lead opted out")
    if sender in (Sender.AGENT, Sender.NURTURE) and lead.is_human_takeover:
        return blocked("human takeover active")
    if (content.text is None) == (content.template_name is None):
        raise ValueError("OutboundContent needs exactly one of text or template_name")
    if sender == Sender.AGENT and context_products is not None and content.text is not None:
        check = validate_reply(content.text, context_products, customer_text)
        if not check.ok:
            return blocked("guardrail: " + "; ".join(check.problems))

    if channel in (Channel.INSTAGRAM, Channel.MESSENGER):
        return _queue_meta(session, lead, content, channel, sender, idempotency_key, now, blocked)
    if channel != Channel.WHATSAPP:
        return blocked(f"channel {channel} not supported")
    if content.private_reply_to or content.public_reply_to:
        return blocked("comment replies are only for Instagram/Messenger")
    if not lead.contact_phone:
        return blocked("lead has no WhatsApp number")

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
        if opens_free_entry:
            lead.free_entry_window_expires_at = now + FREE_ENTRY_WINDOW
            free_entry = True
        category = PricingCategory.FREE_ENTRY if free_entry else PricingCategory.SERVICE
        payload = {"type": "text"}
        preview = content.text

    cost = Decimal("0") if category == PricingCategory.FREE_ENTRY else _rate(category)
    return _record(
        session, lead, channel, sender, preview, content, category, cost, idempotency_key, payload, now
    )


def _record(
    session: Session,
    lead: Lead,
    channel: Channel,
    sender: Sender,
    preview: str | None,
    content: OutboundContent,
    category: PricingCategory,
    cost: Decimal,
    idempotency_key: str,
    payload: dict,
    now: datetime,
) -> SendResult:
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


def last_inbound_at(session: Session, lead: Lead, channel: Channel) -> datetime | None:
    return session.scalar(
        select(Message.timestamp)
        .where(
            Message.lead_id == lead.lead_id,
            Message.channel == channel,
            Message.direction == Direction.INBOUND,
            Message.media_type != "comment",  # a comment does not open the DM window
        )
        .order_by(Message.timestamp.desc())
        .limit(1)
    )


def _queue_meta(
    session: Session,
    lead: Lead,
    content: OutboundContent,
    channel: Channel,
    sender: Sender,
    idempotency_key: str,
    now: datetime,
    blocked,  # noqa: ANN001
) -> SendResult:
    """Instagram / Messenger rules. No per-message fee on these channels."""
    if content.is_template:
        return blocked(f"templates are not available on {channel}")

    if content.private_reply_to:
        created = content.comment_created_at or now
        if now - created > timedelta(days=get_settings().private_reply_window_days):
            return blocked("private reply window (7 days after the comment) has passed")
        payload = {"type": "private_reply", "comment_id": content.private_reply_to}
    elif content.public_reply_to:
        payload = {"type": "public_reply", "comment_id": content.public_reply_to}
    else:
        recipient = lead.instagram_scoped_id if channel == Channel.INSTAGRAM else lead.messenger_scoped_id
        if not recipient:
            return blocked(f"lead has no {channel} ID yet (they have not messaged us there)")
        last = last_inbound_at(session, lead, channel)
        if last is None or now - last > timedelta(hours=24):
            return blocked(f"24-hour {channel} messaging window is closed")
        payload = {"type": "text", "recipient_id": recipient}

    return _record(
        session, lead, channel, sender, content.text, content, PricingCategory.FREE, Decimal("0"),
        idempotency_key, payload, now,
    )  # fmt: skip
