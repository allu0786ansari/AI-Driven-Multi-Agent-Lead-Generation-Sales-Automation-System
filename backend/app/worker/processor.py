"""Background processing of stored webhook events.

The `inbound_events` table is the work queue: no Redis needed, which keeps the
prototype simple to run on Windows. Events are claimed, dispatched through the
router, and marked processed / ignored. A failing handler is retried with
exponential backoff up to `worker_max_attempts`, then marked failed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import or_, select, update

from app.config import get_settings
from app.db.base import utcnow
from app.db.enums import EventStatus
from app.db.models import InboundEvent
from app.db.session import session_scope
from app.logging_config import trace_id_var
from app.router.dispatcher import dispatch

log = logging.getLogger(__name__)

# An event stuck in "processing" this long (e.g. server killed mid-run) is picked up again.
STALE_PROCESSING_AFTER = timedelta(minutes=5)


def _due_condition(now):  # noqa: ANN001, ANN202
    """Pending/retry events whose time has come, or processing events that went stale."""
    return or_(
        InboundEvent.status.in_([EventStatus.PENDING, EventStatus.RETRY])
        & (InboundEvent.next_attempt_at <= now),
        (InboundEvent.status == EventStatus.PROCESSING)
        & (InboundEvent.next_attempt_at <= now - STALE_PROCESSING_AFTER),
    )


def _claim_batch(limit: int) -> list[str]:
    """Atomically move due events to PROCESSING and return their ids."""
    now = utcnow()
    with session_scope() as s:
        candidates = s.scalars(
            select(InboundEvent.id).where(_due_condition(now)).order_by(InboundEvent.received_at).limit(limit)
        ).all()
        claimed: list[str] = []
        for event_id in candidates:
            # Conditional update = claim; if another worker got it first, rowcount is 0.
            # next_attempt_at records when processing started (used for stale detection).
            res = s.execute(
                update(InboundEvent)
                .where(InboundEvent.id == event_id, _due_condition(now))
                .values(status=EventStatus.PROCESSING, next_attempt_at=now)
            )
            if res.rowcount:
                claimed.append(event_id)
        return claimed


def _process_one(event_id: str) -> str:
    settings = get_settings()
    with session_scope() as s:
        event = s.get(InboundEvent, event_id)
        if event is None:
            return "missing"
        token = trace_id_var.set(event.trace_id)
        event.attempts += 1
        try:
            with s.begin_nested():
                result = dispatch(s, event)
            event.status = result.status
            event.error = result.note if result.status == EventStatus.IGNORED else None
            event.processed_at = utcnow()
            log.info("event handled", extra={"event_type": event.event_type, "status": event.status})
        except Exception as exc:  # noqa: BLE001 - any handler failure is retried
            if event.attempts >= settings.worker_max_attempts:
                event.status = EventStatus.FAILED
                event.processed_at = utcnow()
            else:
                event.status = EventStatus.RETRY
                delay = settings.worker_backoff_base_seconds * (2 ** (event.attempts - 1))
                event.next_attempt_at = utcnow() + timedelta(seconds=delay)
            event.error = f"{type(exc).__name__}: {exc}"[:2000]
            log.exception(
                "event handler failed",
                extra={"event_type": event.event_type, "attempt": event.attempts, "status": event.status},
            )
        finally:
            trace_id_var.reset(token)
        return event.status


def process_pending(limit: int = 50) -> dict[str, int]:
    """Process all currently due events once. Returns a count per resulting status."""
    counts: dict[str, int] = {}
    for event_id in _claim_batch(limit):
        status = _process_one(event_id)
        counts[status] = counts.get(status, 0) + 1
    return counts


class Worker:
    """Runs `process_pending` in a loop inside the FastAPI process.

    `wake()` is called by the webhook endpoint so new events are handled within
    milliseconds instead of waiting for the next poll.
    """

    def __init__(self, poll_seconds: float) -> None:
        self.poll_seconds = poll_seconds
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="event-worker")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await self._task

    def wake(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        log.info("worker started")
        while not self._stop.is_set():
            # Clear before working: a wake() that arrives mid-batch is not lost.
            self._wake.clear()
            try:
                while True:
                    counts = await asyncio.to_thread(process_pending)
                    if not counts:
                        break
            except Exception:  # noqa: BLE001 - keep the worker alive
                log.exception("worker loop error")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass
        log.info("worker stopped")
