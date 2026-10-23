"""Outbox delivery: sends `queued` messages after the agent's transaction committed.

Why an outbox: if an agent sent directly and a later step failed, the event
would be retried and the customer would get the message twice. Here the
message row is created atomically with the agent's other changes, and sending
happens afterwards, exactly once:

  queued --claim--> sending --API ok--> sent (then delivered/read via status webhooks)
                            --transient error--> queued again (backoff), up to OUTBOX_MAX_ATTEMPTS
                            --permanent error--> failed

A message left in `sending` (server stopped mid-call) is NOT re-sent - the
customer may already have it - and is marked failed after 10 minutes so it
shows up for a human to check.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, update

from app.alerts.notify import notify_failed
from app.config import get_settings
from app.db.base import utcnow
from app.db.enums import Channel, DeliveryStatus, Direction
from app.db.models import Lead, Message
from app.db.session import session_scope
from app.integrations.errors import ExternalAPIError
from app.integrations.factory import get_meta_messaging, get_whatsapp_sender

log = logging.getLogger(__name__)

STUCK_SENDING_AFTER = timedelta(minutes=10)


def _claim(limit: int) -> list[str]:
    now = utcnow()
    with session_scope() as s:
        ids = s.scalars(
            select(Message.message_id)
            .where(
                Message.direction == Direction.OUTBOUND,
                Message.delivery_status == DeliveryStatus.QUEUED,
                Message.next_attempt_at <= now,
            )
            .order_by(Message.timestamp)
            .limit(limit)
        ).all()
        claimed = []
        for mid in ids:
            res = s.execute(
                update(Message)
                .where(Message.message_id == mid, Message.delivery_status == DeliveryStatus.QUEUED)
                .values(delivery_status=DeliveryStatus.SENDING, next_attempt_at=now)
            )
            if res.rowcount:
                claimed.append(mid)
        return claimed


def _fail_stuck() -> int:
    cutoff = utcnow() - STUCK_SENDING_AFTER
    failed = 0
    with session_scope() as s:
        stuck = s.scalars(
            select(Message).where(
                Message.delivery_status == DeliveryStatus.SENDING, Message.next_attempt_at <= cutoff
            )
        ).all()
        for msg in stuck:
            res = s.execute(  # conditional, in case another worker got there first
                update(Message)
                .where(
                    Message.message_id == msg.message_id, Message.delivery_status == DeliveryStatus.SENDING
                )
                .values(
                    delivery_status=DeliveryStatus.FAILED,
                    error="Delivery outcome unknown: server stopped during sending. Check before resending.",
                )
            )
            if res.rowcount:
                s.refresh(msg)
                notify_failed(s, msg, s.get(Lead, msg.lead_id))
                failed += 1
    return failed


def _dispatch(msg: Message, lead: Lead) -> tuple[str, str | None]:
    """Call the right API for this message. Returns (platform message id, recipient id)."""
    payload = msg.payload or {}
    kind = payload.get("type")
    if msg.channel == Channel.WHATSAPP:
        sender = get_whatsapp_sender()
        if kind == "template":
            receipt = sender.send_template(
                lead.contact_phone, payload["name"], payload["language"], payload.get("params", [])
            )
        else:
            receipt = sender.send_text(lead.contact_phone, msg.text or "")
        return receipt.platform_message_id, None

    meta = get_meta_messaging()
    if kind == "private_reply":
        r = meta.send_private_reply(msg.channel, payload["comment_id"], msg.text or "")
        return r.platform_message_id, r.recipient_id
    if kind == "public_reply":
        return meta.reply_publicly(msg.channel, payload["comment_id"], msg.text or ""), None
    r = meta.send_dm(msg.channel, payload["recipient_id"], msg.text or "")
    return r.platform_message_id, r.recipient_id


def _link_recipient(lead: Lead, channel: str, recipient_id: str | None) -> None:
    """A private reply's response tells us the commenter's messaging ID; keep it for their DMs."""
    if not recipient_id:
        return
    if channel == Channel.MESSENGER and not lead.messenger_scoped_id:
        lead.messenger_scoped_id = recipient_id
    elif channel == Channel.INSTAGRAM and not lead.instagram_scoped_id:
        lead.instagram_scoped_id = recipient_id


def _send_one(message_id: str) -> str:
    settings = get_settings()
    with session_scope() as s:
        msg = s.get(Message, message_id)
        lead = s.get(Lead, msg.lead_id)
        msg.attempts += 1
        try:
            platform_id, recipient_id = _dispatch(msg, lead)
        except ExternalAPIError as exc:
            msg.error = str(exc)[:2000]
            if exc.transient and msg.attempts < settings.outbox_max_attempts:
                msg.delivery_status = DeliveryStatus.QUEUED
                msg.next_attempt_at = utcnow() + timedelta(
                    seconds=settings.outbox_backoff_seconds * 2 ** (msg.attempts - 1)
                )
            else:
                msg.delivery_status = DeliveryStatus.FAILED
                notify_failed(s, msg, lead)
            log.warning("message send failed", extra={"status": msg.delivery_status, "code": exc.code})
            return msg.delivery_status
        except Exception as exc:  # noqa: BLE001 - unexpected bug: fail visibly, never resend blindly
            msg.delivery_status = DeliveryStatus.FAILED
            msg.error = f"{type(exc).__name__}: {exc}"[:2000]
            notify_failed(s, msg, lead)
            log.exception("message send crashed")
            return msg.delivery_status
        msg.platform_message_id = platform_id
        _link_recipient(lead, msg.channel, recipient_id)
        msg.delivery_status = DeliveryStatus.SENT
        msg.sent_at = utcnow()
        msg.error = None
        log.info(
            "message sent",
            extra={"lead_id": lead.lead_id, "channel": msg.channel, "kind": (msg.payload or {}).get("type")},
        )
        return msg.delivery_status


def deliver_pending(limit: int = 50) -> dict[str, int]:
    """Send all due queued messages once. Returns counts per resulting status."""
    counts: dict[str, int] = {}
    stuck = _fail_stuck()
    if stuck:
        counts["failed_stuck"] = stuck
    for mid in _claim(limit):
        status = _send_one(mid)
        counts[status] = counts.get(status, 0) + 1
    return counts
