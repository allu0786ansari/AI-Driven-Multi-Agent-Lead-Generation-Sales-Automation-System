"""Salesperson alerts (spec 3.3: alerts.notify_salesperson).

`notify()` only writes a `notifications` row (inside the agent's transaction,
idempotent by key). `deliver_notifications()` runs in the worker after the
transaction commits and sends them - the same outbox pattern as messages, so
an alert is never lost and never sent twice for the same event.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.alerts.senders import AlertSendError, get_alert_senders
from app.config import get_settings
from app.db.base import utcnow
from app.db.enums import NotificationKind, NotificationStatus
from app.db.models import Lead, Message, Notification
from app.db.session import session_scope
from app.qualify.agent import current_slots
from app.qualify.scoring import summarize

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
TITLES = {
    NotificationKind.HOT_LEAD: "🔥 Hot lead: {who}",
    NotificationKind.HANDOFF: "🙋 Needs a person: {who}",
    NotificationKind.CUSTOMER_REPLIED: "💬 {who} replied (you are handling this lead)",
    NotificationKind.MESSAGE_FAILED: "⚠️ Message not delivered: {who}",
}

_FAILED_WHAT = {
    "template": "WhatsApp template",
    "private_reply": "private reply to their comment",
    "text": "message",
}


def notify_failed(session: Session, message: Message, lead: Lead) -> Notification | None:
    """Tell the salesperson a message did not reach the customer (spec 3.1/3.2, Week 6).

    Raised once per message, whatever reported the failure (the API call, a
    send that never finished, or Meta's status webhook). Not for public replies
    under comments, nor for leads whose data was erased.
    """
    kind = (message.payload or {}).get("type") or "text"
    if kind == "public_reply" or lead.erased_at is not None:
        return None
    what = _FAILED_WHAT.get(kind, "message")
    if message.template_name:
        what = f'WhatsApp template "{message.template_name}"'
    reason = (
        f"The {what} could not be delivered: {message.error or 'no reason given'}. "
        "Check the conversation before sending it again."
    )
    return notify(session, lead, NotificationKind.MESSAGE_FAILED, reason, f"failed:{message.message_id}")


def _who(lead: Lead) -> str:
    contact = lead.contact_phone or (
        f"Instagram {lead.instagram_scoped_id}" if lead.instagram_scoped_id else "Messenger"
    )
    return f"{lead.name or 'Unknown'} ({contact})"


def build_body(lead: Lead, reason: str, last_message: str | None) -> str:
    lines = [
        reason,
        "",
        f"Score: {lead.qualification_score if lead.qualification_score is not None else '-'} ({lead.status})",
        f"Summary: {summarize(current_slots(lead))}",
        f"Source: {lead.source or '-'}" + (f" - {lead.source_detail}" if lead.source_detail else ""),
    ]
    if lead.score_rationale:
        lines.append(f"Why: {lead.score_rationale}")
    if last_message:
        lines.append(f'Last message: "{last_message[:300]}"')
    lines.append(f"Open: {get_settings().dashboard_url.rstrip('/')}/leads/{lead.lead_id}")
    return "\n".join(lines)


def notify(
    session: Session,
    lead: Lead,
    kind: NotificationKind,
    reason: str,
    idempotency_key: str,
    last_message: str | None = None,
) -> Notification:
    existing = session.scalar(select(Notification).where(Notification.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    note = Notification(
        user_id=lead.user_id,
        lead_id=lead.lead_id,
        kind=kind,
        title=TITLES[kind].format(who=_who(lead)),
        body=build_body(lead, reason, last_message),
        idempotency_key=idempotency_key,
        status=NotificationStatus.QUEUED,
    )
    session.add(note)
    session.flush()
    return note


def deliver_notifications(limit: int = 20) -> dict[str, int]:
    counts: dict[str, int] = {}
    now = utcnow()
    with session_scope() as s:
        ids = s.scalars(
            select(Notification.notification_id)
            .where(Notification.status == NotificationStatus.QUEUED, Notification.next_attempt_at <= now)
            .order_by(Notification.created_at)
            .limit(limit)
        ).all()
    for nid in ids:
        status = _deliver_one(nid)
        counts[status] = counts.get(status, 0) + 1
    return counts


def _deliver_one(notification_id: str) -> str:
    senders = get_alert_senders()
    with session_scope() as s:
        # Claim: only one worker may send it.
        claimed = s.execute(
            update(Notification)
            .where(
                Notification.notification_id == notification_id,
                Notification.status == NotificationStatus.QUEUED,
            )
            .values(attempts=Notification.attempts + 1, next_attempt_at=utcnow() + timedelta(minutes=10))
        ).rowcount
    if not claimed:
        return "skipped"

    with session_scope() as s:
        note = s.get(Notification, notification_id)
        if not senders:
            log.warning("ALERT %s | %s", note.title, note.body.replace("\n", " | "))
            note.status = NotificationStatus.LOGGED
            note.sent_at = utcnow()
            return note.status
        done = set(note.channels_sent or [])
        errors = []
        for sender in senders:
            if sender.name in done:
                continue
            try:
                sender.send(note.title, note.body)
                done.add(sender.name)
            except AlertSendError as exc:
                errors.append(str(exc))
        note.channels_sent = sorted(done)
        note.error = "; ".join(errors) or None
        if not errors:
            note.status = NotificationStatus.SENT
            note.sent_at = utcnow()
        elif note.attempts >= MAX_ATTEMPTS:
            note.status = NotificationStatus.SENT if done else NotificationStatus.FAILED
        else:
            note.status = NotificationStatus.QUEUED
            note.next_attempt_at = utcnow() + timedelta(seconds=30 * 2 ** (note.attempts - 1))
        log.info("alert delivery", extra={"status": note.status, "channels": note.channels_sent})
        return note.status
