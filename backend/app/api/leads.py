"""Read-only lead and product-search endpoints (spec Section 3.2).

Week 2 provides what is needed to check the flows; Week 4 adds status
updates, takeover/release and manual messages for the dashboard.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.catalog.links import ContentLinks, get_links, save_links
from app.db.models import Lead, Message, Notification
from app.db.session import get_db
from app.qualify.config import QualificationConfig, get_config, save_config
from app.rag.query import parse_price_constraints
from app.rag.retrieve import get_product_context

router = APIRouter(prefix="/api", tags=["leads"])


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
    whatsapp_opt_in: bool
    opted_out: bool
    is_human_takeover: bool
    messaging_cost_estimate: Decimal
    created_at: datetime
    last_activity_at: datetime


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
    service_window_expires_at: datetime | None
    free_entry_window_expires_at: datetime | None
    instagram_scoped_id: str | None
    messenger_scoped_id: str | None
    qualify_slots: dict[str, Any]
    qualify_state: str | None
    score_rationale: str | None
    messages: list[MessageOut]


@router.get("/leads", response_model=list[LeadSummary])
def list_leads(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[LeadSummary]:
    q = select(Lead).where(Lead.user_id == user.user_id)
    if status:
        q = q.where(Lead.status == status)
    rows = db.scalars(q.order_by(Lead.last_activity_at.desc()).limit(limit)).all()
    return [LeadSummary.model_validate(r, from_attributes=True) for r in rows]


@router.get("/leads/{lead_id}", response_model=LeadDetail)
def get_lead(lead_id: str, user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> LeadDetail:
    lead = db.get(Lead, lead_id)
    if lead is None or lead.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="Lead not found")
    messages = db.scalars(select(Message).where(Message.lead_id == lead_id).order_by(Message.timestamp)).all()
    data = LeadSummary.model_validate(lead, from_attributes=True).model_dump()
    extra = {k: getattr(lead, k) for k in LeadDetail.model_fields if k not in data and k != "messages"}
    return LeadDetail(
        **data, **extra, messages=[MessageOut.model_validate(m, from_attributes=True) for m in messages]
    )


@router.get("/products/search")
def search_products(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str, Query(min_length=1, max_length=500)],
    top_k: Annotated[int, Query(ge=1, le=20)] = 5,
) -> dict[str, Any]:
    """Try the product search the agents use. Shows scores, to tune the catalog and model."""
    filters = parse_price_constraints(q)
    has_filter = filters.max_price is not None or filters.min_price is not None
    hits = get_product_context(
        db, user.user_id, q, top_k=top_k, filters=filters if has_filter else None, min_score=0
    )
    db.commit()  # the search may have (re)built product vectors
    return {
        "query": q,
        "filters": {"min_price": filters.min_price, "max_price": filters.max_price},
        "results": [
            {
                "sku": h.product.sku,
                "name": h.product.name,
                "price": h.product.price,
                "stock_status": h.product.stock_status,
                "score": round(h.score, 3),
                "vector_score": round(h.vector_score, 3),
                "keyword_score": round(h.keyword_score, 3),
            }
            for h in hits
        ],
    }


# ------------------------------------------------------------- Week 3: settings + alerts


@router.get("/settings/qualification", response_model=QualificationConfig)
def get_qualification(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> QualificationConfig:
    """Qualifying questions (asked in this order), scoring weights and hot/warm thresholds."""
    return get_config(db, user.user_id)


@router.put("/settings/qualification", response_model=QualificationConfig)
def put_qualification(
    config: QualificationConfig, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> QualificationConfig:
    """Replace the qualification rules. Applies to the next message each lead sends."""
    save_config(db, user.user_id, config)
    db.commit()
    return config


@router.get("/settings/content-products", response_model=ContentLinks)
def get_content_products(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> ContentLinks:
    """Which product each post / reel / ad shows: {"links": {"<content id>": "<SKU>"}}."""
    return get_links(db, user.user_id)


@router.put("/settings/content-products", response_model=ContentLinks)
def put_content_products(
    links: ContentLinks, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> ContentLinks:
    """Replace all links. Content IDs: Instagram media ID, Facebook post ID, or Click-to-WhatsApp ad ID."""
    unknown = save_links(db, user.user_id, links)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown SKU(s): {', '.join(unknown)}")
    db.commit()
    return links


class NotificationOut(BaseModel):
    notification_id: str
    lead_id: str | None
    kind: str
    title: str
    body: str
    status: str
    channels_sent: list[str]
    error: str | None
    created_at: datetime
    sent_at: datetime | None


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[NotificationOut]:
    """Alerts sent to the salesperson (hot leads, handoffs, replies during takeover), newest first."""
    rows = db.scalars(
        select(Notification)
        .where(Notification.user_id == user.user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    ).all()
    return [NotificationOut.model_validate(r, from_attributes=True) for r in rows]
