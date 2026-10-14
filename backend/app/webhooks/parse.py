"""Split a Meta webhook body into individual, normalized events.

One POST from Meta can carry several entries, and each entry several changes or
messages. Each one becomes a `ParsedEvent` with:

- a stable `platform_event_id` used for de-duplication (Meta retries deliveries)
- a normalized `event_type` used by the router
- a compact `payload` with just the parts the agents need, plus context

Supported payload shapes (Graph API webhooks):

  object = "whatsapp_business_account"
      entry[].changes[] field "messages": value.messages[] / value.statuses[]
  object = "page"
      entry[].changes[] field "leadgen"   -> lead form submission
      entry[].changes[] field "feed"      -> comment added (others skipped)
      entry[].messaging[]                 -> Messenger messages / echoes
  object = "instagram"
      entry[].changes[] field "comments" or "live_comments"
      entry[].messaging[]                 -> Instagram DMs / echoes

Anything unrecognized is kept as `unknown` so it is visible, not silently lost.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.db.enums import EventType


@dataclass(frozen=True)
class ParsedEvent:
    platform_event_id: str
    event_type: EventType
    payload: dict[str, Any]


@dataclass
class ParseResult:
    events: list[ParsedEvent] = field(default_factory=list)
    skipped: int = 0  # recognized but irrelevant (reactions, read receipts, edits)


def _hash_id(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def parse_webhook(body: dict[str, Any]) -> ParseResult:
    result = ParseResult()
    obj = body.get("object")
    entries = body.get("entry")
    if not isinstance(entries, list):
        result.events.append(
            ParsedEvent(f"unknown:{_hash_id(body)}", EventType.UNKNOWN, {"object": obj, "raw": body})
        )
        return result

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id", ""))
        ctx = {"object": obj, "entry_id": entry_id}
        if entry.get("time") is not None:
            ctx["entry_time"] = entry["time"]  # seconds for changes, milliseconds for messaging

        for change in entry.get("changes") or []:
            _parse_change(obj, change, ctx, result)

        for item in entry.get("messaging") or []:
            _parse_messaging(obj, item, ctx, result)

    return result


# --------------------------------------------------------------------- changes


def _parse_change(obj: Any, change: dict[str, Any], ctx: dict[str, Any], out: ParseResult) -> None:
    fld = change.get("field")
    value = change.get("value") or {}

    if obj == "whatsapp_business_account" and fld == "messages":
        _parse_whatsapp_value(value, ctx, out)
    elif obj == "page" and fld == "leadgen":
        leadgen_id = str(value.get("leadgen_id", ""))
        if not leadgen_id:
            out.events.append(_unknown(ctx, change))
            return
        out.events.append(
            ParsedEvent(f"page.leadgen:{leadgen_id}", EventType.PAGE_LEADGEN, {**ctx, "leadgen": value})
        )
    elif obj == "page" and fld == "feed":
        if value.get("item") == "comment" and value.get("verb") == "add" and value.get("comment_id"):
            out.events.append(
                ParsedEvent(
                    f"page.comment:{value['comment_id']}",
                    EventType.PAGE_COMMENT,
                    {**ctx, "comment": value},
                )
            )
        else:
            out.skipped += 1
    elif obj == "instagram" and fld in ("comments", "live_comments"):
        comment_id = value.get("id")
        if not comment_id:
            out.events.append(_unknown(ctx, change))
            return
        out.events.append(
            ParsedEvent(
                f"instagram.comment:{comment_id}",
                EventType.INSTAGRAM_COMMENT,
                {**ctx, "comment": value, "live": fld == "live_comments"},
            )
        )
    else:
        out.events.append(_unknown(ctx, change))


def _parse_whatsapp_value(value: dict[str, Any], ctx: dict[str, Any], out: ParseResult) -> None:
    metadata = value.get("metadata") or {}
    contacts = {c.get("wa_id"): c for c in value.get("contacts") or [] if isinstance(c, dict)}
    wa_ctx = {**ctx, "metadata": metadata}

    for msg in value.get("messages") or []:
        msg_id = msg.get("id")
        if not msg_id:
            out.events.append(_unknown(wa_ctx, msg))
            continue
        out.events.append(
            ParsedEvent(
                f"whatsapp.message:{msg_id}",
                EventType.WHATSAPP_MESSAGE,
                {**wa_ctx, "contact": contacts.get(msg.get("from")), "message": msg},
            )
        )

    for status in value.get("statuses") or []:
        status_id, state = status.get("id"), status.get("status")
        if not status_id or not state:
            out.events.append(_unknown(wa_ctx, status))
            continue
        # The same message moves sent -> delivered -> read; each step is its own event.
        out.events.append(
            ParsedEvent(
                f"whatsapp.status:{status_id}:{state}",
                EventType.WHATSAPP_STATUS,
                {**wa_ctx, "status": status},
            )
        )


# ------------------------------------------------------------------- messaging


def _parse_messaging(obj: Any, item: dict[str, Any], ctx: dict[str, Any], out: ParseResult) -> None:
    if obj == "page":
        msg_type, echo_type, prefix = EventType.PAGE_MESSAGE, EventType.PAGE_MESSAGE_ECHO, "page"
    elif obj == "instagram":
        msg_type, echo_type, prefix = (
            EventType.INSTAGRAM_MESSAGE,
            EventType.INSTAGRAM_MESSAGE_ECHO,
            "instagram",
        )
    else:
        out.events.append(_unknown(ctx, item))
        return

    message = item.get("message")
    postback = item.get("postback")
    if isinstance(message, dict) and message.get("mid"):
        etype = echo_type if message.get("is_echo") else msg_type
        out.events.append(
            ParsedEvent(f"{prefix}.message:{message['mid']}", etype, {**ctx, "messaging": item})
        )
    elif isinstance(postback, dict) and postback.get("mid"):
        out.events.append(
            ParsedEvent(f"{prefix}.message:{postback['mid']}", msg_type, {**ctx, "messaging": item})
        )
    else:
        # read receipts, deliveries, reactions, typing indicators
        out.skipped += 1


def _unknown(ctx: dict[str, Any], raw: Any) -> ParsedEvent:
    return ParsedEvent(f"unknown:{_hash_id([ctx, raw])}", EventType.UNKNOWN, {**ctx, "raw": raw})
