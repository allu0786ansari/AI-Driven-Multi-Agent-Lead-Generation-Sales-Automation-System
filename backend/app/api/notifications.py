"""Alerts for the salesperson, as shown in the dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.db.models import Notification
from app.db.session import get_db
from app.leads.service import mark_alerts_read

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    notification_id: str
    lead_id: str | None
    kind: str
    title: str
    body: str
    status: str
    channels_sent: list[str]
    error: str | None
    created_at: datetime
    sent_at: datetime | None
    read_at: datetime | None


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    unread: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[NotificationOut]:
    """Alerts (hot leads, handoffs, replies during takeover), newest first."""
    q = select(Notification).where(Notification.user_id == user.user_id)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    rows = db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)).all()
    return [NotificationOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/unread-count")
def unread_count(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> dict[str, int]:
    n = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.user_id, Notification.read_at.is_(None))
    )
    return {"unread": n or 0}


class MarkRead(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=500)
    lead_id: str | None = None
    all: bool = False


@router.post("/read")
def mark_read(body: MarkRead, user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> dict[str, int]:
    """Mark alerts as read: specific ids, all alerts for one lead, or everything (all=true)."""
    if not (body.ids or body.lead_id or body.all):
        return {"marked": 0}
    marked = mark_alerts_read(db, user.user_id, lead_id=body.lead_id, ids=body.ids)
    db.commit()
    return {"marked": marked}
