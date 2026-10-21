"""Follow-ups for the dashboard: what is planned, and a manual "check now"."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.db.base import utcnow
from app.db.enums import Sender
from app.db.models import Message
from app.db.session import get_db
from app.nurture.agent import planned, run_nurture

router = APIRouter(prefix="/api/nurture", tags=["follow-ups"])


class UpcomingItem(BaseModel):
    lead_id: str
    name: str | None
    product_interest: str | None
    due_at: datetime
    step: int  # 1 = first follow-up


class UpcomingOut(BaseModel):
    upcoming: list[UpcomingItem]
    sent_last_7_days: int


@router.get("/upcoming", response_model=UpcomingOut)
def upcoming(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> UpcomingOut:
    """Follow-ups planned next (earliest first, worked out now) and how many were sent in the last 7 days."""
    rows = planned(db, user.user_id, limit)
    sent = db.scalar(
        select(func.count()).where(
            Message.user_id == user.user_id,
            Message.sender == Sender.NURTURE,
            Message.timestamp >= utcnow() - timedelta(days=7),
        )
    )
    return UpcomingOut(
        upcoming=[
            UpcomingItem(
                lead_id=lead.lead_id,
                name=lead.name,
                product_interest=lead.product_interest,
                due_at=plan.due_at,
                step=plan.step + 1,
            )
            for lead, plan in rows
        ],
        sent_last_7_days=sent or 0,
    )


@router.post("/run")
async def run_now(user: CurrentUser, request: Request) -> dict[str, int]:
    """Check for due follow-ups now instead of waiting for the next automatic check."""
    counts = await run_in_threadpool(run_nurture, None, user.user_id)
    worker = getattr(request.app.state, "worker", None)
    if worker is not None and counts.get("sent"):
        worker.wake()
    return counts
