"""Database tables - spec Section 4 (and users from Section 8.3).

Every business table carries `user_id` (the business/tenant) from day one, so
multi-tenancy later needs no data migration.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime, new_id, utcnow
from app.db.enums import DeliveryStatus, EventStatus, LeadStatus, StockStatus


class User(Base):
    """A business account (tenant). The prototype has exactly one."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    business_name: Mapped[str] = mapped_column(String(255), default="")
    product_category: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Lead(Base):
    """Spec Section 4.1. Mirrored to Google Sheets from Week 3."""

    __tablename__ = "leads"
    __table_args__ = (
        Index(None, "user_id", "status"),
        Index(None, "user_id", "contact_phone"),
        Index(None, "user_id", "instagram_scoped_id"),
        Index(None, "user_id", "meta_lead_id"),
        Index(None, "user_id", "nurture_next_at"),
        Index(None, "user_id", "messenger_scoped_id"),
        Index(None, "user_id", "facebook_user_id"),
    )

    lead_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))

    # Identity / contact
    name: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(32))  # E.164
    contact_email: Mapped[str | None] = mapped_column(String(255))
    instagram_scoped_id: Mapped[str | None] = mapped_column(String(64))
    messenger_scoped_id: Mapped[str | None] = mapped_column(String(64))
    # Facebook commenter ID (differs from the Messenger PSID; linked via the private reply)
    facebook_user_id: Mapped[str | None] = mapped_column(String(64))
    preferred_channel: Mapped[str | None] = mapped_column(String(16))
    language: Mapped[str | None] = mapped_column(String(16))

    # Source and attribution
    source: Mapped[str | None] = mapped_column(String(32))
    source_detail: Mapped[str | None] = mapped_column(Text)
    meta_lead_id: Mapped[str | None] = mapped_column(String(64))
    ad_id: Mapped[str | None] = mapped_column(String(64))
    campaign_id: Mapped[str | None] = mapped_column(String(64))
    ctwa_clid: Mapped[str | None] = mapped_column(String(255))
    # When the lead last messaged us from a Click-to-WhatsApp ad. Replying within
    # 24h of this opens the 72h free entry window (set by the Send Gate).
    ctwa_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Qualification
    product_interest: Mapped[str | None] = mapped_column(String(255))
    qualify_state: Mapped[str | None] = mapped_column(String(64))
    qualify_slots: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    qualification_score: Mapped[int | None] = mapped_column(Integer)
    score_rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=LeadStatus.NEW)
    assigned_to: Mapped[str | None] = mapped_column(String(255))
    is_human_takeover: Mapped[bool] = mapped_column(Boolean, default=False)
    conversation_summary: Mapped[str | None] = mapped_column(Text)

    # Consent
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_in_source: Mapped[str | None] = mapped_column(String(32))
    opt_in_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    opted_out_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Messaging windows
    last_inbound_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    service_window_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    free_entry_window_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Nurture
    nurture_step: Mapped[int] = mapped_column(Integer, default=0)
    nurture_next_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    messaging_cost_estimate: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Product(Base):
    """Spec Section 4.2. Feeds the vector store from Week 2."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("user_id", "sku"),
        Index(None, "user_id", "category"),
    )

    product_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    sku: Mapped[str] = mapped_column(String(64))  # business's own code; stable import key
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(128))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    description: Mapped[str] = mapped_column(Text, default="")
    key_attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stock_status: Mapped[str] = mapped_column(String(16), default=StockStatus.IN_STOCK)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    last_updated: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Message(Base):
    """Spec Section 4.3. One row per message on any channel.

    Outbound rows double as the outbox (docs/DECISIONS.md D-009): the Send Gate
    writes them as `queued` in the same transaction as the agent's other
    changes; the outbox worker sends them after that transaction commits.
    """

    __tablename__ = "messages"
    __table_args__ = (
        Index(None, "lead_id", "timestamp"),
        Index(None, "platform_message_id"),
        Index(None, "delivery_status", "next_attempt_at"),
        UniqueConstraint("idempotency_key"),
    )

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.lead_id"))
    channel: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(16))
    sender: Mapped[str] = mapped_column(String(16))
    text: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(16), default="none")
    platform_message_id: Mapped[str | None] = mapped_column(String(255))
    template_name: Mapped[str | None] = mapped_column(String(255))
    pricing_category: Mapped[str | None] = mapped_column(String(16))
    delivery_status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    # Outbox fields (outbound only)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # template params, language
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)


class ProductEmbedding(Base):
    """Vector for one product, stored in the main database (docs/DECISIONS.md D-007).

    `text_hash` detects when the product's searchable text changed, so only
    changed products are re-embedded; `model` detects an embedding model switch.
    """

    __tablename__ = "product_embeddings"
    __table_args__ = (Index(None, "user_id", "model"),)

    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.product_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    model: Mapped[str] = mapped_column(String(128))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[float]] = mapped_column(JSON)
    text_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ConsentEvent(Base):
    """Spec Section 4.4. Append-only audit trail of opt-ins and opt-outs."""

    __tablename__ = "consent_events"
    __table_args__ = (Index(None, "lead_id", "timestamp"),)

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.lead_id"))
    type: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(64))
    wording_shown: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class InboundEvent(Base):
    """Stored webhook events: de-duplication + durable work queue.

    Replaces spec Section 4.5 `processed_events` (see docs/DECISIONS.md D-002).
    One webhook POST from Meta can contain several events; each becomes a row.
    """

    __tablename__ = "inbound_events"
    __table_args__ = (
        UniqueConstraint("platform_event_id"),
        Index(None, "status", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    platform_event_id: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.user_id"))
    endpoint: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    trace_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=EventStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)  # last failure
    result_note: Mapped[str | None] = mapped_column(Text)  # what the handler did (shown in /api/events)


class BusinessSetting(Base):
    """Per-business configuration stored as JSON (qualification rules, sync state, ...)."""

    __tablename__ = "business_settings"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Notification(Base):
    """Alerts for the salesperson (hot lead, handoff, ...). Sent by the worker like the outbox.

    The Week 4 dashboard reads the same rows for its live notification feed.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        Index(None, "user_id", "created_at"),
        Index(None, "status", "next_attempt_at"),
    )

    notification_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.lead_id"))
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    channels_sent: Mapped[list[str]] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    error: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # dashboard (Week 4)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class LeadActivity(Base):
    """What people (and the system) did to a lead: status changes, takeover, release, manual messages.

    Shown on the lead page; also the record of when a lead was marked converted
    (for analytics and the Meta feedback loop later).
    """

    __tablename__ = "lead_activity"
    __table_args__ = (Index(None, "lead_id", "created_at"),)

    activity_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.lead_id"))
    actor: Mapped[str] = mapped_column(String(255))  # salesperson email, or "system"
    kind: Mapped[str] = mapped_column(String(32))  # status | assign | takeover | release | message
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
