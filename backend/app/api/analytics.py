"""Analytics for the dashboard (spec 3.2). Definitions: app/analytics/service.py."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.analytics import service
from app.auth.deps import CurrentUser
from app.db.session import get_db

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

Days = Annotated[int, Query(ge=1, le=365, description="Last N days (business time zone)")]


@router.get("/summary")
def summary(user: CurrentUser, db: Annotated[Session, Depends(get_db)], days: Days = 30) -> dict[str, Any]:
    """Leads per day, funnel, reply/conversion rates, first-response time, follow-ups, hot-label agreement."""
    return service.summary(db, user.user_id, days)


@router.get("/costs")
def costs(user: CurrentUser, db: Annotated[Session, Depends(get_db)], days: Days = 30) -> dict[str, Any]:
    """Estimated messaging cost by day, channel and category; per lead, qualified lead and sale."""
    return service.costs(db, user.user_id, days)
