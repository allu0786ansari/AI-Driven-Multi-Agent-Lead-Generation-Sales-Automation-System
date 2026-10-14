"""Engage Agent: decides what to do with a customer's message on any channel.

Order of checks for a message (WhatsApp, Instagram DM, Messenger):
  opt-out word          -> record opt-out, confirm once, stop
  opted out             -> stay silent
  human takeover        -> stay silent, tell the salesperson the customer wrote
  asks for a person     -> holding reply, hand off, alert the salesperson
  voice note / photo    -> holding reply, hand off, alert
  text                  -> product answer from the catalog (responder), ending
                           with the next qualifying question; then the Qualify
                           Agent updates the score and alerts on a new hot lead

Comments are handled in app/agents/comments.py.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.agents.capture import CaptureResult
from app.agents.responder import ReplyOutcome, fallback_reply, generate_product_reply, retrieve_for_message
from app.alerts.notify import notify
from app.catalog.links import apply_link, linked_product
from app.db.base import utcnow
from app.db.enums import Channel, ConsentType, EventStatus, NotificationKind, Sender
from app.db.models import ConsentEvent, Lead
from app.messaging.send_gate import Decision, OutboundContent, SendResult, queue_message
from app.qualify.agent import QualifyResult, preview_next_question, process_message
from app.qualify.slots import wants_human
from app.router.dispatcher import HandlerResult

log = logging.getLogger(__name__)

OPT_OUT_WORDS = {"stop", "stop messages", "unsubscribe", "cancel", "band", "band karo", "opt out", "optout"}
OPT_OUT_CONFIRMATION = (
    "You won't receive any more messages from us. If you change your mind, just send us a message here."
)
HUMAN_REQUEST_REPLY = "Sure! A member of our team will contact you shortly."
CHANNEL_LABELS = {
    Channel.WHATSAPP: "WhatsApp",
    Channel.INSTAGRAM: "Instagram",
    Channel.MESSENGER: "Messenger",
}

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


def record_opt_out(session: Session, lead: Lead, source: str, channel: str = Channel.WHATSAPP) -> None:
    now = utcnow()
    lead.opted_out = True
    lead.opted_out_at = now
    lead.nurture_next_at = None
    session.add(
        ConsentEvent(
            user_id=lead.user_id,
            lead_id=lead.lead_id,
            type=ConsentType.OPT_OUT,
            channel=channel,
            source=source,
            timestamp=now,
        )
    )


def hand_off(session: Session, lead: Lead, reason: str, key: str, last_message: str | None = None) -> None:
    """Give the conversation to a person and alert them."""
    lead.is_human_takeover = True
    notify(session, lead, NotificationKind.HANDOFF, reason, f"handoff:{key}", last_message)


def _alert_if_hot(session: Session, lead: Lead, q: QualifyResult, text: str | None) -> None:
    if q.became_hot:
        notify(
            session,
            lead,
            NotificationKind.HOT_LEAD,
            f"Lead scored {q.score.score} and is now HOT.",
            f"hot:{lead.lead_id}:{lead.qualification_score}:{utcnow().date()}",
            text,
        )


def respond_to_message(session: Session, capture: CaptureResult, channel: str) -> HandlerResult:
    lead = capture.lead
    msg_key = capture.message.platform_message_id if capture.message else lead.lead_id
    key = f"reply:{msg_key}"
    text = capture.text

    def queue(content: OutboundContent, sender: Sender = Sender.AGENT, **kw) -> SendResult:  # noqa: ANN003
        return queue_message(
            session, lead, content, idempotency_key=key, sender=sender, channel=channel, **kw
        )

    if is_opt_out(text):
        record_opt_out(session, lead, "keyword", channel)
        queue(OutboundContent(text=OPT_OUT_CONFIRMATION), Sender.SYSTEM, allow_opted_out=True)
        return HandlerResult(note="opted out")
    if lead.opted_out:
        return HandlerResult(note="lead opted out; no reply")
    if lead.is_human_takeover:
        notify(
            session, lead, NotificationKind.CUSTOMER_REPLIED, "The customer sent a new message.",
            f"replied:{msg_key}", text or f"<{capture.media_type}>",
        )  # fmt: skip
        return HandlerResult(note="human takeover; no automatic reply (salesperson notified)")

    if wants_human(text):
        q = process_message(session, lead, text or "")
        result = queue(OutboundContent(text=HUMAN_REQUEST_REPLY), Sender.SYSTEM)
        hand_off(session, lead, "The customer asked to talk to a person.", msg_key, text)
        return HandlerResult(
            note=f"asked for a person: handed off ({result.decision}); score {q.score.score}"
        )

    if capture.media_type != "none" and not text:
        label = MEDIA_LABELS.get(capture.media_type, "message")
        result = queue(
            OutboundContent(
                text=f"Thanks for the {label}! A member of our team will check it and reply shortly."
            ),
            Sender.SYSTEM,
        )
        hand_off(session, lead, f"The customer sent a {label} the assistant cannot read.", msg_key)
        return HandlerResult(note=f"{label} handed off to a human ({result.decision})")

    if channel == Channel.WHATSAPP and lead.ad_id and not lead.product_interest:
        linked = linked_product(session, lead.user_id, lead.ad_id)  # Click-to-WhatsApp ad -> product
        if linked is not None:
            apply_link(lead, linked)
    retrieved = retrieve_for_message(session, lead.user_id, text or "", lead.product_interest)
    question = preview_next_question(session, lead, text or "", retrieved[0])
    outcome = generate_product_reply(
        session,
        lead,
        text or "",
        capture.message.message_id if capture.message else None,
        channel_label=CHANNEL_LABELS.get(channel, "WhatsApp"),
        next_question=question,
        retrieved=retrieved,
    )
    q = process_message(session, lead, text or "", outcome.extracted, outcome.hits)
    if outcome.language and not lead.language:
        lead.language = outcome.language

    result = _queue_reply(queue, outcome, text or "", question)
    if outcome.needs_human:
        hand_off(session, lead, "The assistant could not answer and handed over.", msg_key, text)
    _alert_if_hot(session, lead, q, text)

    note = f"reply {outcome.source}: {result.decision}" + (f" ({result.reason})" if result.reason else "")
    note += f"; score {q.score.score} {q.status}"
    if outcome.notes:
        note += " | " + "; ".join(outcome.notes)[:300]
    log.info(
        "reply decided", extra={"channel": channel, "source": outcome.source, "decision": result.decision}
    )
    return HandlerResult(EventStatus.PROCESSED, note)


def _queue_reply(queue, outcome: ReplyOutcome, customer_text: str, question: str | None) -> SendResult:  # noqa: ANN001
    holding = outcome.source == "holding"
    result = queue(
        OutboundContent(text=outcome.text),
        Sender.SYSTEM if holding else Sender.AGENT,
        context_products=None if holding else outcome.products,
        customer_text=customer_text,
    )
    if result.decision == Decision.BLOCKED and outcome.source == "llm":
        # The gate's own guardrail check disagreed: send the catalog-only version instead.
        result = queue(
            OutboundContent(text=fallback_reply(outcome.products, question)),
            context_products=outcome.products,
            customer_text=customer_text,
        )
    return result


def respond_to_whatsapp(session: Session, capture: CaptureResult) -> HandlerResult:
    return respond_to_message(session, capture, Channel.WHATSAPP)
