"""Lead stages back to Meta, so ads optimise for leads that buy (proposal Phase 6, spec Section 3.3).

Two Conversions API flavours, chosen per lead (docs/DECISIONS.md D-038):

- Lead-form leads (have Meta's 15-17 digit lead ID) -> "Conversions API for CRM":
  action_source "system_generated", user_data.lead_id, custom_data
  {event_source: "crm", lead_event_source: <META_FEEDBACK_SOURCE_NAME>}, and the
  stage name as event_name: Lead, Qualified, Contacted, Bought, Lost. Every stage
  is sent, including the first one; set up the same stage names in Events Manager.
- Click-to-WhatsApp leads (have a ctwa_clid) -> "Conversions API for business
  messaging": action_source "business_messaging", messaging_channel "whatsapp",
  user_data {whatsapp_business_account_id, ctwa_clid}, standard events only:
  hot -> QualifiedLead, Bought -> Purchase (with value and currency, worked out
  from the catalog price x quantity; skipped when the value is unknown).

Events are queued in meta_events in the same transaction as the stage change and
sent by the worker (like the outbox): retried on temporary errors, each stage
once per lead. Off unless META_FEEDBACK_ENABLED=true; with META_API_MODE=fake
they are only recorded (status "logged"). Erasing a lead's data cancels its
queued events.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import utcnow
from app.db.models import Lead, MetaEvent, Product
from app.db.session import session_scope
from app.integrations.errors import ExternalAPIError
from app.integrations.graph_http import request

log = logging.getLogger(__name__)

CRM_EVENT = {
    "lead": "Lead",
    "qualified": "Qualified",
    "contacted": "Contacted",
    "bought": "Bought",
    "lost": "Lost",
}
MESSAGING_EVENT = {"qualified": "QualifiedLead", "bought": "Purchase"}
MAX_ATTEMPTS = 5


def _valid_lead_id(value: str | None) -> bool:
    return bool(value) and value.isdigit() and 15 <= len(value) <= 17


def _order_value(session: Session, lead: Lead) -> Decimal | None:
    """Catalog price x quantity for what the customer wanted; None if unknown."""
    slots = lead.qualify_slots or {}
    qty = int(slots.get("quantity") or 1)
    price = None
    if slots.get("product"):
        price = session.scalar(
            select(Product.price).where(Product.user_id == lead.user_id, Product.name == slots["product"])
        )
    if price is None and (slots.get("category") or lead.product_interest):
        price = session.scalar(
            select(func.min(Product.price)).where(
                Product.user_id == lead.user_id,
                Product.category == (slots.get("category") or lead.product_interest),
            )
        )
    return (Decimal(str(price)) * qty).quantize(Decimal("0.01")) if price is not None else None


def queue_stage(session: Session, lead: Lead, stage: str) -> list[MetaEvent]:
    """Queue the events for `stage` that apply to this lead (none if feedback is off)."""
    s = get_settings()
    if not s.meta_feedback_enabled or lead.erased_at is not None:
        return []
    now = utcnow()
    events: list[MetaEvent] = []

    def add(kind: str, name: str, payload: dict[str, Any]) -> None:
        key = f"capi:{kind}:{lead.lead_id}:{stage}"
        if session.scalar(select(MetaEvent.event_id).where(MetaEvent.idempotency_key == key)):
            return
        ev = MetaEvent(
            user_id=lead.user_id, lead_id=lead.lead_id, kind=kind, stage=stage, event_name=name,
            payload=payload, idempotency_key=key, next_attempt_at=now,
        )  # fmt: skip
        session.add(ev)
        events.append(ev)

    if stage in CRM_EVENT and _valid_lead_id(lead.meta_lead_id):
        add(
            "crm",
            CRM_EVENT[stage],
            {
                "event_name": CRM_EVENT[stage],
                "event_time": int(now.timestamp()),
                "action_source": "system_generated",
                "user_data": {"lead_id": int(lead.meta_lead_id)},
                "custom_data": {"event_source": "crm", "lead_event_source": s.meta_feedback_source_name},
            },
        )
    if stage in MESSAGING_EVENT and lead.ctwa_clid and s.whatsapp_business_account_id:
        payload: dict[str, Any] = {
            "event_name": MESSAGING_EVENT[stage],
            "event_time": int(now.timestamp()),
            "action_source": "business_messaging",
            "messaging_channel": "whatsapp",
            "user_data": {
                "whatsapp_business_account_id": s.whatsapp_business_account_id,
                "ctwa_clid": lead.ctwa_clid,
            },
        }
        if stage == "bought":
            value = _order_value(session, lead)
            if value is None:
                log.info("purchase not reported: order value unknown", extra={"lead_id": lead.lead_id})
            else:
                payload["custom_data"] = {"currency": "INR", "value": float(value)}
                add("messaging", MESSAGING_EVENT[stage], payload)
        else:
            add("messaging", MESSAGING_EVENT[stage], payload)
    if events:
        session.flush()
    return events


def cancel_for_lead(session: Session, lead_id: str) -> int:
    """Personal data erased: drop queued events and the identifiers in all of them."""
    rows = session.scalars(select(MetaEvent).where(MetaEvent.lead_id == lead_id)).all()
    for ev in rows:
        if ev.status == "queued":
            ev.status = "cancelled"
            ev.error = "cancelled: personal data erased"
        ev.payload = {}
    return len(rows)


# ------------------------------------------------------------------------- sending


def _post(event: MetaEvent) -> None:
    s = get_settings()
    if not s.meta_dataset_id:
        raise ExternalAPIError("META_DATASET_ID is not set", transient=False)
    token = s.meta_capi_access_token or s.meta_page_access_token
    url = f"{s.meta_graph_base_url.rstrip('/')}/{s.meta_graph_version}/{s.meta_dataset_id}/events"
    body: dict[str, Any] = {"data": [event.payload]}
    if event.kind == "messaging":
        body["partner_agent"] = s.meta_feedback_source_name
    with httpx.Client(timeout=s.meta_api_timeout_seconds) as http:
        request(http, "POST", url, "Conversions API", params={"access_token": token}, json=body)


def _send_one(event_id: str) -> str:
    with session_scope() as s:
        claimed = s.execute(
            update(MetaEvent)
            .where(MetaEvent.event_id == event_id, MetaEvent.status == "queued")
            .values(attempts=MetaEvent.attempts + 1, next_attempt_at=utcnow() + timedelta(minutes=10))
        ).rowcount
    if not claimed:
        return "skipped"
    with session_scope() as s:
        ev = s.get(MetaEvent, event_id)
        if get_settings().meta_api_mode != "live":
            ev.status, ev.sent_at = "logged", utcnow()
            log.info("meta event recorded (fake mode)", extra={"event": ev.event_name, "kind": ev.kind})
            return ev.status
        try:
            _post(ev)
        except ExternalAPIError as exc:
            ev.error = str(exc)[:1000]
            if exc.transient and ev.attempts < MAX_ATTEMPTS:
                ev.status = "queued"
                ev.next_attempt_at = utcnow() + timedelta(minutes=2**ev.attempts)
            else:
                ev.status = "failed"
            log.warning("meta event not sent", extra={"event": ev.event_name, "error": ev.error[:200]})
            return ev.status
        ev.status, ev.sent_at, ev.error = "sent", utcnow(), None
        return ev.status


def deliver_meta_events(limit: int = 20) -> dict[str, int]:
    now = utcnow()
    with session_scope() as s:
        ids = s.scalars(
            select(MetaEvent.event_id)
            .where(MetaEvent.status == "queued", MetaEvent.next_attempt_at <= now)
            .order_by(MetaEvent.created_at)
            .limit(limit)
        ).all()
    counts: dict[str, int] = {}
    for eid in ids:
        status = _send_one(eid)
        counts[status] = counts.get(status, 0) + 1
    return counts
