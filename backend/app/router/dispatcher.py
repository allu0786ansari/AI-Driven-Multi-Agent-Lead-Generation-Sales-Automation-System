"""Deterministic event router (proposal Section 3: "Router", no LLM).

The event type, set during webhook parsing, decides which handler runs.
Agents register their handlers here as they are built:

    Week 2: whatsapp.message -> Capture/Qualify, page.leadgen -> Capture,
            whatsapp.status -> message log update
    Week 3: *.comment -> Engage, *.message -> Engage/Qualify
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import EventStatus, EventType
from app.db.models import InboundEvent

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HandlerResult:
    status: EventStatus = EventStatus.PROCESSED
    note: str | None = None


Handler = Callable[[Session, InboundEvent], HandlerResult | None]

_HANDLERS: dict[str, Handler] = {}

# Events we receive but never act on: our own outgoing messages echoed back.
_ALWAYS_IGNORED = {
    EventType.PAGE_MESSAGE_ECHO.value,
    EventType.INSTAGRAM_MESSAGE_ECHO.value,
    EventType.UNKNOWN.value,
}


def register(event_type: EventType) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        if event_type.value in _HANDLERS:
            raise ValueError(f"Handler already registered for {event_type.value}")
        _HANDLERS[event_type.value] = fn
        return fn

    return decorator


def registered_types() -> list[str]:
    return sorted(_HANDLERS)


def clear_handlers() -> None:
    """For tests."""
    _HANDLERS.clear()


def dispatch(session: Session, event: InboundEvent) -> HandlerResult:
    """Run the handler for this event. Exceptions propagate to the worker (which retries)."""
    if event.event_type in _ALWAYS_IGNORED:
        return HandlerResult(EventStatus.IGNORED, "not actionable")
    handler = _HANDLERS.get(event.event_type)
    if handler is None:
        log.info("no handler registered", extra={"event_type": event.event_type})
        return HandlerResult(EventStatus.IGNORED, f"no handler for {event.event_type}")
    result = handler(session, event)
    return result if result is not None else HandlerResult()


def summarize(event: InboundEvent) -> dict[str, Any]:
    return {"id": event.id, "event_type": event.event_type, "attempts": event.attempts}
