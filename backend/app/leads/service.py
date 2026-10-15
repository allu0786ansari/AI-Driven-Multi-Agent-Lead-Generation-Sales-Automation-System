"""What a salesperson can do with a lead from the dashboard (spec Section 3.2).

Every action is recorded in lead_activity. Manual messages go through the
Send Gate exactly like the agents' messages (sender = salesperson), so the
same window, consent and opt-out rules apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.enums import Channel, Direction, LeadStatus, PricingCategory, Sender
from app.db.models import Lead, LeadActivity, Message, Notification
from app.messaging.send_gate import OutboundContent, SendResult, last_inbound_at, queue_message

DM_WINDOW = timedelta(hours=24)
CLOSED_STATUSES = (LeadStatus.CONVERTED, LeadStatus.LOST)


def record(session: Session, lead: Lead, actor: str, kind: str, **detail: Any) -> None:
    session.add(
        LeadActivity(user_id=lead.user_id, lead_id=lead.lead_id, actor=actor, kind=kind, detail=detail)
    )


# ------------------------------------------------------------------ lead state


def set_status(session: Session, lead: Lead, status: LeadStatus, actor: str) -> None:
    if lead.status == status:
        return
    record(session, lead, actor, "status", old=lead.status, new=status)
    lead.status = status
    if status in CLOSED_STATUSES:
        lead.nurture_next_at = None  # Week 5: no follow-ups for closed leads


def assign(session: Session, lead: Lead, assigned_to: str | None, actor: str) -> None:
    assigned_to = (assigned_to or "").strip() or None
    if lead.assigned_to == assigned_to:
        return
    record(session, lead, actor, "assign", old=lead.assigned_to, new=assigned_to)
    lead.assigned_to = assigned_to


def take_over(session: Session, lead: Lead, actor: str, reason: str = "manual") -> None:
    if lead.is_human_takeover:
        return
    lead.is_human_takeover = True
    lead.assigned_to = lead.assigned_to or actor
    record(session, lead, actor, "takeover", reason=reason)


def release(session: Session, lead: Lead, actor: str) -> None:
    """Hand the conversation back to the assistant. It answers the customer's next message."""
    if not lead.is_human_takeover:
        return
    lead.is_human_takeover = False
    record(session, lead, actor, "release")


def mark_alerts_read(
    session: Session, user_id: str, lead_id: str | None = None, ids: list[str] | None = None
) -> int:
    q = select(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    if lead_id:
        q = q.where(Notification.lead_id == lead_id)
    if ids:
        q = q.where(Notification.notification_id.in_(ids))
    rows = session.scalars(q).all()
    now = utcnow()
    for n in rows:
        n.read_at = now
    return len(rows)


# --------------------------------------------------------------- reply options


@dataclass
class ReplyOption:
    channel: str
    free_text: bool  # can type a normal message now
    templates: bool  # can send an approved WhatsApp template
    open_until: datetime | None
    reason: str  # why free text is not possible (empty when it is)


def reply_options(session: Session, lead: Lead, now: datetime | None = None) -> list[ReplyOption]:
    now = now or utcnow()
    options: list[ReplyOption] = []
    opted_out = "This customer opted out of messages." if lead.opted_out else ""

    if lead.contact_phone:
        until = lead.service_window_expires_at
        open_ = until is not None and until > now
        reason = opted_out or (
            ""
            if open_
            else "More than 24 hours since their last WhatsApp message. Send an approved template."
        )
        options.append(ReplyOption(Channel.WHATSAPP, open_ and not opted_out, not opted_out, until, reason))

    for channel, scoped in (
        (Channel.INSTAGRAM, lead.instagram_scoped_id),
        (Channel.MESSENGER, lead.messenger_scoped_id),
    ):
        if not scoped:
            continue
        last = last_inbound_at(session, lead, channel)
        until = last + DM_WINDOW if last else None
        open_ = until is not None and until > now
        name = "Instagram" if channel == Channel.INSTAGRAM else "Messenger"
        reason = opted_out or (
            ""
            if open_
            else f"They haven't messaged you on {name} in the last 24 hours. "
            "You can reply after they write again."
        )
        options.append(ReplyOption(channel, open_ and not opted_out, False, until, reason))
    return options


def default_channel(session: Session, lead: Lead) -> str | None:
    """The channel of the customer's latest message (not counting comments)."""
    channel = session.scalar(
        select(Message.channel)
        .where(
            Message.lead_id == lead.lead_id,
            Message.direction == Direction.INBOUND,
            Message.media_type != "comment",
        )
        .order_by(Message.timestamp.desc())
        .limit(1)
    )
    return channel or lead.preferred_channel


# -------------------------------------------------------------- manual message


def send_manual(
    session: Session,
    lead: Lead,
    actor: str,
    channel: str,
    client_message_id: str,
    text: str | None = None,
    template_name: str | None = None,
    template_params: list[str] | None = None,
    template_category: PricingCategory = PricingCategory.MARKETING,
) -> SendResult:
    """Queue a salesperson's message. Taking part in the chat takes it over from the assistant."""
    content = (
        OutboundContent(
            template_name=template_name,
            template_params=template_params or [],
            template_category=template_category,
        )
        if template_name
        else OutboundContent(text=(text or "").strip())
    )
    result = queue_message(
        session,
        lead,
        content,
        idempotency_key=f"manual:{client_message_id}",
        sender=Sender.SALESPERSON,
        channel=Channel(channel),
    )
    if result.message is not None and result.reason != "already queued (idempotent)":
        take_over(session, lead, actor, reason="replied manually")
        record(session, lead, actor, "message", channel=channel, template=template_name)
    return result


# ----------------------------------------------------------------- inbox data


def latest_messages(session: Session, lead_ids: list[str]) -> dict[str, Message]:
    if not lead_ids:
        return {}
    latest = (
        select(Message.lead_id, func.max(Message.timestamp).label("ts"))
        .where(Message.lead_id.in_(lead_ids))
        .group_by(Message.lead_id)
        .subquery()
    )
    rows = session.scalars(
        select(Message).join(
            latest, and_(Message.lead_id == latest.c.lead_id, Message.timestamp == latest.c.ts)
        )
    ).all()
    return {m.lead_id: m for m in rows}


def unread_alert_counts(session: Session, user_id: str, lead_ids: list[str]) -> dict[str, int]:
    if not lead_ids:
        return {}
    rows = session.execute(
        select(Notification.lead_id, func.count())
        .where(
            Notification.user_id == user_id,
            Notification.lead_id.in_(lead_ids),
            Notification.read_at.is_(None),
        )
        .group_by(Notification.lead_id)
    ).all()
    return dict(rows)


def needs_you_condition():  # noqa: ANN201
    """A person is handling it, or it is hot and nobody has contacted it yet."""
    return (Lead.is_human_takeover.is_(True) & Lead.status.not_in(CLOSED_STATUSES)) | (
        Lead.status == LeadStatus.HOT
    )
