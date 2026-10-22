"""Router handlers for WhatsApp events."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.capture import capture_whatsapp_message
from app.agents.engage import respond_to_whatsapp
from app.alerts.notify import notify_failed
from app.db.enums import DELIVERY_RANK, DeliveryStatus, EventStatus, EventType, PricingCategory
from app.db.models import InboundEvent, Lead, Message
from app.db.tenancy import resolve_user_id
from app.router.dispatcher import HandlerResult, register

# A status can arrive a moment before our "sent" record is committed; retry a few times.
STATUS_LOOKUP_ATTEMPTS = 3


@register(EventType.WHATSAPP_MESSAGE)
def on_whatsapp_message(session: Session, event: InboundEvent) -> HandlerResult:
    user_id = event.user_id or resolve_user_id(session)
    if user_id is None:
        return HandlerResult(EventStatus.IGNORED, "no business user exists yet")
    event.user_id = user_id
    capture = capture_whatsapp_message(session, user_id, event.payload)
    result = respond_to_whatsapp(session, capture)
    prefix = "new lead" if capture.created else "existing lead"
    return HandlerResult(result.status, f"{prefix} {capture.lead.lead_id}: {result.note}")


@register(EventType.WHATSAPP_STATUS)
def on_whatsapp_status(session: Session, event: InboundEvent) -> HandlerResult:
    status = event.payload["status"]
    msg = session.scalar(select(Message).where(Message.platform_message_id == status["id"]))
    if msg is None:
        if event.attempts < STATUS_LOOKUP_ATTEMPTS:
            raise LookupError(f"no message with id {status['id']} yet")
        return HandlerResult(EventStatus.IGNORED, "status for a message not sent by this system")

    new = status.get("status")
    if new == DeliveryStatus.FAILED:
        errors = status.get("errors") or [{}]
        err = errors[0]
        msg.delivery_status = DeliveryStatus.FAILED
        msg.error = f"{err.get('code')}: {err.get('title') or err.get('message') or 'failed'}"
        notify_failed(session, msg, session.get(Lead, msg.lead_id))
    elif new in DELIVERY_RANK and DELIVERY_RANK[DeliveryStatus(new)] > DELIVERY_RANK.get(
        msg.delivery_status, -1
    ):
        msg.delivery_status = new

    # Meta tells us how the message was actually billed; correct our estimate.
    pricing = status.get("pricing") or {}
    if pricing:
        free = pricing.get("billable") is False or pricing.get("type") in (
            "free_entry_point",
            "free_customer_service",
        )
        if pricing.get("type") == "free_entry_point":
            msg.pricing_category = PricingCategory.FREE_ENTRY
        elif pricing.get("category") in {c.value for c in PricingCategory}:
            msg.pricing_category = pricing["category"]
        if free and msg.estimated_cost:
            lead = session.get(Lead, msg.lead_id)
            lead.messaging_cost_estimate = Decimal(str(lead.messaging_cost_estimate)) - Decimal(
                str(msg.estimated_cost)
            )
            msg.estimated_cost = Decimal("0")
    return HandlerResult(note=f"message {msg.message_id} -> {msg.delivery_status}")
