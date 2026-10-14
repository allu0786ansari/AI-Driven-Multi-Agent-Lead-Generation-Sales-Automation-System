"""Router handlers for Facebook / Instagram comments and Messenger / Instagram DMs (Week 3)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.capture import capture_meta_message, parse_comment
from app.agents.comments import handle_comment
from app.agents.engage import respond_to_message
from app.db.enums import Channel, EventStatus, EventType
from app.db.models import InboundEvent
from app.db.tenancy import resolve_user_id
from app.router.dispatcher import HandlerResult, register


def _user(session: Session, event: InboundEvent) -> str | None:
    user_id = event.user_id or resolve_user_id(session)
    event.user_id = user_id
    return user_id


@register(EventType.INSTAGRAM_COMMENT)
@register(EventType.PAGE_COMMENT)
def on_comment(session: Session, event: InboundEvent) -> HandlerResult:
    user_id = _user(session, event)
    if user_id is None:
        return HandlerResult(EventStatus.IGNORED, "no business user exists yet")
    return handle_comment(session, user_id, parse_comment(event.event_type, event.payload))


@register(EventType.INSTAGRAM_MESSAGE)
@register(EventType.PAGE_MESSAGE)
def on_direct_message(session: Session, event: InboundEvent) -> HandlerResult:
    user_id = _user(session, event)
    if user_id is None:
        return HandlerResult(EventStatus.IGNORED, "no business user exists yet")
    channel = Channel.INSTAGRAM if event.event_type == EventType.INSTAGRAM_MESSAGE else Channel.MESSENGER
    capture = capture_meta_message(session, user_id, channel, event.payload)
    result = respond_to_message(session, capture, channel)
    prefix = "new lead" if capture.created else "existing lead"
    return HandlerResult(result.status, f"{prefix} {capture.lead.lead_id}: {result.note}")
