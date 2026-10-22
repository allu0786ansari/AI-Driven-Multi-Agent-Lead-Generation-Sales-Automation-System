"""Health check and webhook event inspection."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app import __version__
from app.auth.deps import CurrentUser
from app.config import get_settings
from app.db.models import InboundEvent
from app.db.session import get_db
from app.llm.factory import get_llm_client
from app.router.dispatcher import registered_types

router = APIRouter(prefix="/api", tags=["system"])


def _last_backup() -> str | None:
    from app.backup import BackupError, list_backups

    try:
        latest = next(iter(list_backups()), None)
    except (BackupError, OSError):
        return None
    return latest.created_at.isoformat() if latest else None


@router.get("/health")
async def health(
    db: Annotated[Session, Depends(get_db)],
    deep: Annotated[bool, Query(description="Also check that the LLM is reachable")] = False,
) -> dict[str, Any]:
    settings = get_settings()
    try:
        await run_in_threadpool(db.execute, text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    result: dict[str, Any] = {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "env": settings.app_env,
        "database": "ok" if db_ok else "error",
        "llm_provider": settings.llm_provider,
        "handlers": registered_types(),
        "last_backup": _last_backup(),
    }
    if deep:
        llm_ok = await run_in_threadpool(get_llm_client().health)
        result["llm"] = "ok" if llm_ok else "unreachable or model not pulled"
        if not llm_ok:
            result["status"] = "degraded"
    return result


class EventOut(BaseModel):
    id: str
    platform_event_id: str
    endpoint: str
    event_type: str
    status: str
    attempts: int
    error: str | None
    result_note: str | None  # what the handler did (Week 2); was missing from the API until Week 4
    trace_id: str
    received_at: datetime
    processed_at: datetime | None


class EventsResponse(BaseModel):
    counts_by_status: dict[str, int]
    events: list[EventOut]


@router.get("/events", response_model=EventsResponse)
def list_events(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    status: str | None = None,
) -> EventsResponse:
    """Recent webhook events for the logged-in business (newest first)."""
    # Events that arrived before any user existed have no user_id; show them too.
    scope = or_(InboundEvent.user_id == user.user_id, InboundEvent.user_id.is_(None))
    q = select(InboundEvent).where(scope)
    if status:
        q = q.where(InboundEvent.status == status)
    rows = db.scalars(q.order_by(InboundEvent.received_at.desc()).limit(limit)).all()
    counts = dict(
        db.execute(select(InboundEvent.status, func.count()).where(scope).group_by(InboundEvent.status)).all()
    )
    return EventsResponse(
        counts_by_status=counts,
        events=[EventOut.model_validate(r, from_attributes=True) for r in rows],
    )
