"""Erase a customer's personal data on request (spec 3.2, DELETE /api/leads/{id}/personal-data).

Removed: name, phone, email, every platform ID, message text and raw message
payloads, the raw webhook events that came from this customer, alert texts,
the conversation summary and the details they gave (city, budget, ...).

Kept, without anything that identifies the person: the lead row with source,
channel, product interest, score, status and dates; message rows with
direction, channel, sender, template name, delivery status and cost; consent
records (proof of what was agreed, without contact details). This keeps the
analytics and cost totals correct (docs/DECISIONS.md D-032).

Messages still waiting in the outbox are cancelled, and the lead is marked
opted out so nothing can be sent to it again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.enums import DeliveryStatus
from app.db.models import InboundEvent, Lead, Message, Notification
from app.leads.service import record

ERASED = "[erased]"
_KEEP_SLOTS = ("product", "category", "quantity", "timeline", "purchase_intent")


@dataclass
class EraseResult:
    messages: int
    events: int
    alerts: int
    cancelled: int


def _identifiers(lead: Lead) -> list[str]:
    ids = [
        lead.contact_phone, lead.contact_email, lead.instagram_scoped_id, lead.messenger_scoped_id,
        lead.facebook_user_id, lead.meta_lead_id, lead.ctwa_clid,
    ]  # fmt: skip
    out = []
    for value in ids:
        if value:
            out.append(value)
            if value.startswith("+"):
                out.append(value[1:])  # WhatsApp payloads carry the number without "+"
    return [v for v in out if len(v) >= 6]  # never match on short, ambiguous values


def erase_personal_data(session: Session, lead: Lead, actor: str) -> EraseResult:
    identifiers = _identifiers(lead)
    message_ids = []

    messages = session.scalars(select(Message).where(Message.lead_id == lead.lead_id)).all()
    cancelled = 0
    for m in messages:
        if m.platform_message_id:
            message_ids.append(m.platform_message_id)
        if m.delivery_status in (DeliveryStatus.QUEUED, DeliveryStatus.SENDING):
            m.delivery_status = DeliveryStatus.FAILED
            m.error = "cancelled: personal data erased"
            cancelled += 1
        m.text = ERASED if m.text else None
        m.payload = {"type": (m.payload or {}).get("type")} if m.payload else {}
        m.platform_message_id = None

    # Raw webhook events from this customer (they contain the number, name and text).
    needles = [n for n in identifiers + message_ids if n]
    events = 0
    if needles:
        rows = session.scalars(select(InboundEvent).where(InboundEvent.user_id == lead.user_id)).all()
        for e in rows:
            raw = json.dumps(e.payload, ensure_ascii=False)
            if any(n in raw for n in needles):
                e.payload = {"erased": True}
                e.result_note = ERASED
                e.error = None
                events += 1

    alerts = 0
    for n in session.scalars(select(Notification).where(Notification.lead_id == lead.lead_id)).all():
        n.title = "Lead"
        n.body = ERASED
        n.read_at = n.read_at or utcnow()
        alerts += 1

    slots = lead.qualify_slots or {}
    lead.qualify_slots = {k: slots[k] for k in _KEEP_SLOTS if slots.get(k) is not None}
    for column in (
        "name", "contact_phone", "contact_email", "instagram_scoped_id", "messenger_scoped_id",
        "facebook_user_id", "meta_lead_id", "ctwa_clid", "conversation_summary", "source_detail",
        "assigned_to", "score_rationale",
    ):  # fmt: skip
        setattr(lead, column, None)
    lead.opted_out = True
    lead.opted_out_at = lead.opted_out_at or utcnow()
    lead.is_human_takeover = False
    lead.nurture_next_at = None
    lead.erased_at = utcnow()
    record(session, lead, actor, "erase", messages=len(messages), events=events)
    session.flush()
    return EraseResult(len(messages), events, alerts, cancelled)
