"""Store parsed webhook events, dropping duplicates.

The webhook request only parses and stores; all real work happens later in the
background worker. That keeps the response to Meta fast (Meta retries if we are
slow) and means nothing is lost if processing fails or the server restarts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import InboundEvent
from app.db.tenancy import resolve_user_id
from app.logging_config import new_trace_id
from app.webhooks.parse import parse_webhook

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    stored: int = 0
    duplicates: int = 0
    skipped: int = 0
    event_ids: list[str] = field(default_factory=list)


def ingest_webhook(session: Session, endpoint: str, body: dict[str, Any]) -> IngestResult:
    parsed = parse_webhook(body)
    result = IngestResult(skipped=parsed.skipped)
    if not parsed.events:
        return result

    ids = [e.platform_event_id for e in parsed.events]
    existing = set(
        session.scalars(select(InboundEvent.platform_event_id).where(InboundEvent.platform_event_id.in_(ids)))
    )
    user_id = resolve_user_id(session, body)
    seen_in_batch: set[str] = set()

    for ev in parsed.events:
        if ev.platform_event_id in existing or ev.platform_event_id in seen_in_batch:
            result.duplicates += 1
            continue
        seen_in_batch.add(ev.platform_event_id)
        row = InboundEvent(
            platform_event_id=ev.platform_event_id,
            user_id=user_id,
            endpoint=endpoint,
            event_type=ev.event_type.value,
            payload=ev.payload,
            trace_id=new_trace_id(),
        )
        try:
            # Savepoint per row: a concurrent duplicate only skips that one event.
            with session.begin_nested():
                session.add(row)
        except IntegrityError:
            result.duplicates += 1
            continue
        result.stored += 1
        result.event_ids.append(row.id)
        log.info(
            "webhook event stored",
            extra={"event_type": ev.event_type.value, "event_trace_id": row.trace_id},
        )

    session.commit()
    return result
