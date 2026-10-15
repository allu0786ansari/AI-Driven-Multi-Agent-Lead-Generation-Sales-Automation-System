"""Lead endpoints for the dashboard (spec Section 3.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.db.enums import Channel, LeadStatus, PricingCategory
from app.db.models import Lead, LeadActivity, Message
from app.db.session import get_db
from app.leads import service
from app.messaging.send_gate import Decision

router = APIRouter(prefix="/api", tags=["leads"])

View = Literal["needs_you", "open", "closed", "all"]


class LeadSummary(BaseModel):
    lead_id: str
    name: str | None
    contact_phone: str | None
    source: str | None
    product_interest: str | None
    status: str
    qualification_score: int | None
    conversation_summary: str | None
    preferred_channel: str | None
    assigned_to: str | None
    whatsapp_opt_in: bool
    opted_out: bool
    is_human_takeover: bool
    messaging_cost_estimate: Decimal
    created_at: datetime
    last_activity_at: datetime
    # Week 4: inbox extras
    last_message_text: str | None = None
    last_message_at: datetime | None = None
    last_message_direction: str | None = None
    last_message_sender: str | None = None
    last_message_channel: str | None = None
    unread_alerts: int = 0


class MessageOut(BaseModel):
    message_id: str
    channel: str
    direction: str
    sender: str
    text: str | None
    media_type: str
    template_name: str | None
    pricing_category: str | None
    delivery_status: str
    estimated_cost: Decimal
    error: str | None
    timestamp: datetime
    kind: str | None = None  # text | template | private_reply | public_reply | comment | message


class ActivityOut(BaseModel):
    activity_id: str
    actor: str
    kind: str
    detail: dict[str, Any]
    created_at: datetime


class ReplyOptionOut(BaseModel):
    channel: str
    free_text: bool
    templates: bool
    open_until: datetime | None
    reason: str


class LeadDetail(LeadSummary):
    contact_email: str | None
    source_detail: str | None
    meta_lead_id: str | None
    ad_id: str | None
    campaign_id: str | None
    ctwa_clid: str | None
    language: str | None
    opt_in_source: str | None
    opt_in_at: datetime | None
    opted_out_at: datetime | None
    service_window_expires_at: datetime | None
    free_entry_window_expires_at: datetime | None
    instagram_scoped_id: str | None
    messenger_scoped_id: str | None
    facebook_user_id: str | None
    qualify_slots: dict[str, Any]
    qualify_state: str | None
    score_rationale: str | None
    messages: list[MessageOut]
    activity: list[ActivityOut]
    reply_options: list[ReplyOptionOut]
    default_channel: str | None


def _get_lead(db: Session, user_id: str, lead_id: str) -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None or lead.user_id != user_id:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _filtered(user_id: str, view: View | None, status: str | None, channel: str | None, q: str | None):  # noqa: ANN202
    query = select(Lead).where(Lead.user_id == user_id)
    if view == "needs_you":
        query = query.where(service.needs_you_condition())
    elif view == "open":
        query = query.where(Lead.status.not_in(service.CLOSED_STATUSES))
    elif view == "closed":
        query = query.where(Lead.status.in_(service.CLOSED_STATUSES))
    if status:
        query = query.where(Lead.status == status)
    if channel:
        query = query.where(Lead.preferred_channel == channel)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                Lead.name.ilike(like),
                Lead.contact_phone.ilike(like),
                Lead.contact_email.ilike(like),
                Lead.product_interest.ilike(like),
                Lead.conversation_summary.ilike(like),
            )
        )
    return query


@router.get("/leads", response_model=list[LeadSummary])
def list_leads(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    view: View | None = None,
    status: str | None = None,
    channel: str | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LeadSummary]:
    """Leads, most recent activity first. view: needs_you | open | closed | all."""
    rows = db.scalars(
        _filtered(user.user_id, view, status, channel, q)
        .order_by(Lead.last_activity_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    ids = [r.lead_id for r in rows]
    latest = service.latest_messages(db, ids)
    unread = service.unread_alert_counts(db, user.user_id, ids)
    out = []
    for r in rows:
        item = LeadSummary.model_validate(r, from_attributes=True)
        m = latest.get(r.lead_id)
        if m is not None:
            item.last_message_text = m.text or (f"<{m.media_type}>" if m.media_type != "none" else None)
            item.last_message_at = m.timestamp
            item.last_message_direction = m.direction
            item.last_message_sender = m.sender
            item.last_message_channel = m.channel
        item.unread_alerts = unread.get(r.lead_id, 0)
        out.append(item)
    return out


@router.get("/leads/counts")
def lead_counts(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> dict[str, int]:
    """Numbers for the inbox tabs."""
    by_status = dict(
        db.execute(
            select(Lead.status, func.count()).where(Lead.user_id == user.user_id).group_by(Lead.status)
        ).all()
    )

    def count(cond) -> int:  # noqa: ANN001
        return (
            db.scalar(select(func.count()).select_from(Lead).where(Lead.user_id == user.user_id, cond)) or 0
        )

    total = sum(by_status.values())
    closed = sum(by_status.get(s, 0) for s in service.CLOSED_STATUSES)
    return {
        "all": total,
        "needs_you": count(service.needs_you_condition()),
        "open": total - closed,
        "closed": closed,
        "takeover": count(Lead.is_human_takeover.is_(True)),
        **{s.value: by_status.get(s.value, 0) for s in LeadStatus},
    }


@router.get("/leads/{lead_id}", response_model=LeadDetail)
def get_lead(lead_id: str, user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> LeadDetail:
    lead = _get_lead(db, user.user_id, lead_id)
    messages = db.scalars(select(Message).where(Message.lead_id == lead_id).order_by(Message.timestamp)).all()
    activity = db.scalars(
        select(LeadActivity).where(LeadActivity.lead_id == lead_id).order_by(LeadActivity.created_at)
    ).all()
    data = LeadSummary.model_validate(lead, from_attributes=True).model_dump()
    data["unread_alerts"] = service.unread_alert_counts(db, user.user_id, [lead_id]).get(lead_id, 0)
    skip = {"messages", "activity", "reply_options", "default_channel"}
    extra = {k: getattr(lead, k) for k in LeadDetail.model_fields if k not in data and k not in skip}
    out_messages = []
    for m in messages:
        item = MessageOut.model_validate(m, from_attributes=True)
        item.kind = "comment" if m.media_type == "comment" else (m.payload or {}).get("type")
        out_messages.append(item)
    return LeadDetail(
        **data,
        **extra,
        messages=out_messages,
        activity=[ActivityOut.model_validate(a, from_attributes=True) for a in activity],
        reply_options=[ReplyOptionOut(**vars(o)) for o in service.reply_options(db, lead)],
        default_channel=service.default_channel(db, lead),
    )


class LeadPatch(BaseModel):
    status: LeadStatus | None = None
    assigned_to: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=255)


@router.patch("/leads/{lead_id}", response_model=LeadSummary)
def update_lead(
    lead_id: str, patch: LeadPatch, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> LeadSummary:
    """Change status (e.g. contacted, converted, lost), who it is assigned to, or the name."""
    lead = _get_lead(db, user.user_id, lead_id)
    fields = patch.model_fields_set
    if "status" in fields and patch.status is not None:
        service.set_status(db, lead, patch.status, user.email)
    if "assigned_to" in fields:
        service.assign(db, lead, patch.assigned_to, user.email)
    if "name" in fields and patch.name:
        lead.name = patch.name.strip()
    db.commit()
    return LeadSummary.model_validate(lead, from_attributes=True)


@router.post("/leads/{lead_id}/takeover", response_model=LeadSummary)
def take_over(lead_id: str, user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> LeadSummary:
    """You handle this conversation; the assistant stops replying to this lead."""
    lead = _get_lead(db, user.user_id, lead_id)
    service.take_over(db, lead, user.email)
    db.commit()
    return LeadSummary.model_validate(lead, from_attributes=True)


@router.post("/leads/{lead_id}/release", response_model=LeadSummary)
def release(lead_id: str, user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> LeadSummary:
    """Hand the conversation back to the assistant; it answers the customer's next message."""
    lead = _get_lead(db, user.user_id, lead_id)
    service.release(db, lead, user.email)
    db.commit()
    return LeadSummary.model_validate(lead, from_attributes=True)


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    params: list[str] = Field(default_factory=list, max_length=10)
    category: Literal["marketing", "utility"] = "marketing"


class ManualMessageIn(BaseModel):
    channel: Channel | None = None  # default: where the customer last wrote
    text: str | None = Field(default=None, max_length=4096)
    template: TemplateIn | None = None
    client_message_id: str = Field(default_factory=lambda: uuid.uuid4().hex, max_length=64)

    @model_validator(mode="after")
    def _one(self) -> ManualMessageIn:
        has_text = bool(self.text and self.text.strip())
        if has_text == (self.template is not None):
            raise ValueError("Send either text or a template")
        return self


class ManualMessageOut(BaseModel):
    decision: str  # queued | needs_template | blocked
    reason: str
    message: MessageOut | None = None


@router.post("/leads/{lead_id}/message", response_model=ManualMessageOut)
def send_message(
    lead_id: str,
    body: ManualMessageIn,
    request: Request,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ManualMessageOut:
    """Send your own message. Uses the same rules as the assistant (24h windows, opt-out, templates).

    Replying takes the conversation over from the assistant. Repeating a request
    with the same client_message_id never sends twice.
    """
    lead = _get_lead(db, user.user_id, lead_id)
    channel = body.channel or service.default_channel(db, lead) or Channel.WHATSAPP
    t = body.template
    result = service.send_manual(
        db, lead, user.email, channel, body.client_message_id,
        text=body.text,
        template_name=t.name if t else None,
        template_params=t.params if t else None,
        template_category=PricingCategory(t.category) if t else PricingCategory.MARKETING,
    )  # fmt: skip
    db.commit()
    if result.decision == Decision.QUEUED:
        worker = getattr(request.app.state, "worker", None)
        if worker is not None:
            worker.wake()  # send right away instead of at the next poll
    msg = None
    if result.message is not None:
        msg = MessageOut.model_validate(result.message, from_attributes=True)
        msg.kind = (result.message.payload or {}).get("type")
    return ManualMessageOut(decision=result.decision, reason=result.reason, message=msg)
