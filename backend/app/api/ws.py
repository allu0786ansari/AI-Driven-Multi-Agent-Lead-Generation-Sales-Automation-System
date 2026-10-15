"""Live updates for the dashboard (spec 3.2: WS /ws/notifications).

The browser connects with ?token=<JWT> (browsers cannot set headers on
WebSockets). Every POLL_SECONDS the server checks the database and pushes:

  {"type": "hello", "unread": 3}
  {"type": "notification", "notification": {...}}      new alert
  {"type": "leads_changed", "lead_ids": [...]}         new messages / status changes

Polling the database (instead of in-memory events) means updates from any
process are seen - the worker, a second server, or a script. The client may
send "ping"; the server answers "pong".
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select

from app.auth.security import decode_access_token
from app.db.base import utcnow
from app.db.models import Lead, Notification, User
from app.db.session import session_scope

log = logging.getLogger(__name__)
router = APIRouter()

POLL_SECONDS = 1.5
# Rows are stamped before their transaction commits, so look back a little and de-duplicate.
OVERLAP = timedelta(seconds=15)
UNAUTHORIZED = 4401


def _user_for(token: str) -> str | None:
    try:
        user_id = decode_access_token(token)
    except jwt.PyJWTError:
        return None
    with session_scope() as s:
        user = s.get(User, user_id)
        return user_id if user is not None and user.is_active else None


def _unread(user_id: str) -> int:
    with session_scope() as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            )
            or 0
        )


def _changes(user_id: str, since: datetime) -> tuple[list[dict], list[tuple[str, datetime]]]:
    with session_scope() as s:
        notes = s.scalars(
            select(Notification)
            .where(Notification.user_id == user_id, Notification.created_at > since)
            .order_by(Notification.created_at)
        ).all()
        leads = s.execute(
            select(Lead.lead_id, Lead.last_activity_at).where(
                Lead.user_id == user_id, Lead.last_activity_at > since
            )
        ).all()
        payload = [
            {
                "notification_id": n.notification_id,
                "lead_id": n.lead_id,
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "created_at": n.created_at.isoformat(),
            }
            for n in notes
        ]
        return payload, [(lid, ts) for lid, ts in leads]


@router.websocket("/ws/notifications")
async def notifications_ws(ws: WebSocket, token: str = "") -> None:
    user_id = await run_in_threadpool(_user_for, token)
    if user_id is None:
        await ws.close(code=UNAUTHORIZED, reason="Invalid or expired token")
        return
    await ws.accept()
    started = utcnow()
    seen_notes: deque[str] = deque(maxlen=500)
    seen_leads: dict[str, datetime] = {}
    await ws.send_json({"type": "hello", "unread": await run_in_threadpool(_unread, user_id)})

    try:
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), timeout=POLL_SECONDS)
                if text == "ping":
                    await ws.send_text("pong")
                continue
            except TimeoutError:
                pass
            since = max(started, utcnow() - OVERLAP)
            notes, leads = await run_in_threadpool(_changes, user_id, since)
            for n in notes:
                if n["notification_id"] not in seen_notes:
                    seen_notes.append(n["notification_id"])
                    await ws.send_json({"type": "notification", "notification": n})
            changed = [lid for lid, ts in leads if seen_leads.get(lid) != ts]
            for lid, ts in leads:
                seen_leads[lid] = ts
            if changed:
                await ws.send_json({"type": "leads_changed", "lead_ids": changed})
    except WebSocketDisconnect:
        return
