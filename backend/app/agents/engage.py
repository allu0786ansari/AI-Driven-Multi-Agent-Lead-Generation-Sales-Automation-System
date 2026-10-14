"""Decides what to do with a customer's WhatsApp message (Week 2 scope).

Order of checks:
  opt-out word   -> record opt-out, confirm once, stop
  opted out      -> stay silent
  human takeover -> stay silent (the salesperson is handling it)
  voice note / image / other media -> hand off to a human with a holding reply
  text           -> product answer from the catalog (responder)

Qualification questions and scoring are added in Week 3.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.agents.capture import CaptureResult
from app.agents.responder import fallback_reply, generate_product_reply
from app.db.base import utcnow
from app.db.enums import Channel, ConsentType, EventStatus, Sender
from app.db.models import ConsentEvent, Lead
from app.messaging.send_gate import Decision, OutboundContent, queue_message
from app.router.dispatcher import HandlerResult

log = logging.getLogger(__name__)

OPT_OUT_WORDS = {"stop", "stop messages", "unsubscribe", "cancel", "band", "band karo", "opt out", "optout"}
OPT_OUT_CONFIRMATION = (
    "You won't receive any more messages from us. If you change your mind, just send us a message here."
)

MEDIA_LABELS = {
    "audio": "voice note",
    "image": "photo",
    "video": "video",
    "document": "document",
    "sticker": "sticker",
    "location": "location",
    "contacts": "contact",
}


def is_opt_out(text: str | None) -> bool:
    if not text:
        return False
    cleaned = re.sub(r"[^\w\s]", "", text).strip().casefold()
    return cleaned in OPT_OUT_WORDS


def record_opt_out(session: Session, lead: Lead, source: str) -> None:
    now = utcnow()
    lead.opted_out = True
    lead.opted_out_at = now
    lead.nurture_next_at = None
    session.add(
        ConsentEvent(
            user_id=lead.user_id,
            lead_id=lead.lead_id,
            type=ConsentType.OPT_OUT,
            channel=Channel.WHATSAPP,
            source=source,
            timestamp=now,
        )
    )


def hand_off(lead: Lead) -> None:
    """Give the conversation to a person. Week 3 adds the salesperson alert."""
    lead.is_human_takeover = True


def respond_to_whatsapp(session: Session, capture: CaptureResult) -> HandlerResult:
    lead = capture.lead
    key = f"reply:{capture.message.platform_message_id}" if capture.message else f"reply:{lead.lead_id}"

    if is_opt_out(capture.text):
        record_opt_out(session, lead, "keyword")
        queue_message(
            session,
            lead,
            OutboundContent(text=OPT_OUT_CONFIRMATION),
            idempotency_key=key,
            sender=Sender.SYSTEM,
            allow_opted_out=True,
        )
        return HandlerResult(note="opted out")
    if lead.opted_out:
        return HandlerResult(note="lead opted out; no reply")
    if lead.is_human_takeover:
        return HandlerResult(note="human takeover; no automatic reply")

    if capture.media_type != "none" and not capture.text:
        label = MEDIA_LABELS.get(capture.media_type, "message")
        hand_off(lead)
        result = queue_message(
            session,
            lead,
            OutboundContent(
                text=f"Thanks for the {label}! A member of our team will check it and reply shortly."
            ),
            idempotency_key=key,
            sender=Sender.SYSTEM,
        )
        return HandlerResult(note=f"{label} handed off to a human ({result.decision})")

    outcome = generate_product_reply(
        session, lead, capture.text or "", capture.message.message_id if capture.message else None
    )
    if outcome.language and not lead.language:
        lead.language = outcome.language
    if outcome.products and not lead.product_interest:
        lead.product_interest = outcome.products[0].category

    result = queue_message(
        session,
        lead,
        OutboundContent(text=outcome.text),
        idempotency_key=key,
        context_products=outcome.products if outcome.source != "holding" else None,
        customer_text=capture.text or "",
        sender=Sender.AGENT if outcome.source != "holding" else Sender.SYSTEM,
    )
    if result.decision == Decision.BLOCKED and outcome.source == "llm":
        # The gate's own guardrail check disagreed: send the catalog-only version instead.
        result = queue_message(
            session,
            lead,
            OutboundContent(text=fallback_reply(outcome.products)),
            idempotency_key=key,
            context_products=outcome.products,
            customer_text=capture.text or "",
        )
    if outcome.needs_human:
        hand_off(lead)
    note = f"reply {outcome.source}: {result.decision}" + (f" ({result.reason})" if result.reason else "")
    if outcome.notes:
        note += " | " + "; ".join(outcome.notes)[:300]
    status = EventStatus.PROCESSED
    log.info("whatsapp reply decided", extra={"source": outcome.source, "decision": result.decision})
    return HandlerResult(status, note)
