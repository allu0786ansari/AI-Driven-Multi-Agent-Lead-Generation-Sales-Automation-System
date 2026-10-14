"""Capture Agent (proposal Section 3a): turn any inbound event into one lead record.

- normalizes phone numbers to E.164
- finds the existing lead for this business, or creates one
- records attribution (ad, campaign, CTWA click ID, Meta lead ID)
- records consent from lead forms
- logs the inbound message and updates the messaging windows
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import utcnow
from app.db.enums import Channel, ConsentType, DeliveryStatus, Direction, LeadSource, OptInSource, Sender
from app.db.models import ConsentEvent, Lead, Message

SERVICE_WINDOW = timedelta(hours=24)
_TRUTHY = {"yes", "y", "true", "1", "checked", "agree", "i agree", "haan", "ha", "ok"}


@dataclass
class CaptureResult:
    lead: Lead
    created: bool
    message: Message | None = None
    text: str | None = None
    media_type: str = "none"


def normalize_phone(raw: str | None, default_country_code: str | None = None) -> str | None:
    """'+91 98765-43210', '09876543210', '9876543210', '919876543210' -> '+919876543210'."""
    if not raw:
        return None
    cc = default_country_code or get_settings().default_country_code
    raw = raw.strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        result = digits
    elif digits.startswith("00"):
        result = digits[2:]
    elif len(digits) == 10:
        result = cc + digits
    elif len(digits) == 11 and digits.startswith("0"):
        result = cc + digits[1:]
    else:
        result = digits
    return f"+{result}" if 11 <= len(result) <= 15 else None


def find_or_create_by_phone(session: Session, user_id: str, phone: str) -> tuple[Lead, bool]:
    lead = session.scalar(
        select(Lead)
        .where(Lead.user_id == user_id, Lead.contact_phone == phone)
        .order_by(Lead.created_at)
        .limit(1)
    )
    if lead is not None:
        return lead, False
    lead = Lead(user_id=user_id, contact_phone=phone, preferred_channel=Channel.WHATSAPP, qualify_slots={})
    session.add(lead)
    session.flush()
    return lead, True


def _epoch(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError):
        return utcnow()


def extract_whatsapp_text(msg: dict[str, Any]) -> tuple[str | None, str]:
    """Returns (text, media_type). Quick-reply button taps count as text."""
    kind = msg.get("type", "unknown")
    if kind == "text":
        return (msg.get("text") or {}).get("body"), "none"
    if kind == "button":
        return (msg.get("button") or {}).get("text"), "none"
    if kind == "interactive":
        inter = msg.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return reply.get("title"), "none"
    caption = (msg.get(kind) or {}).get("caption") if isinstance(msg.get(kind), dict) else None
    return caption, kind


def capture_whatsapp_message(session: Session, user_id: str, payload: dict[str, Any]) -> CaptureResult:
    msg = payload["message"]
    phone = normalize_phone("+" + str(msg.get("from", "")))
    if phone is None:
        raise ValueError(f"WhatsApp message without a valid sender number: {msg.get('from')!r}")
    lead, created = find_or_create_by_phone(session, user_id, phone)

    profile_name = ((payload.get("contact") or {}).get("profile") or {}).get("name")
    if profile_name and not lead.name:
        lead.name = profile_name
    lead.preferred_channel = lead.preferred_channel or Channel.WHATSAPP

    sent_at = _epoch(msg.get("timestamp"))
    referral = msg.get("referral") or {}
    if referral:
        if referral.get("source_type") == "ad" and referral.get("source_id"):
            lead.ad_id = str(referral["source_id"])
        if referral.get("ctwa_clid"):
            lead.ctwa_clid = referral["ctwa_clid"]
            lead.ctwa_message_at = sent_at
        if referral.get("headline"):
            lead.source_detail = f"CTWA ad: {referral['headline']}"
    if lead.source is None:
        lead.source = LeadSource.CTWA_AD if referral.get("ctwa_clid") else LeadSource.DM

    if lead.last_inbound_at is None or sent_at > lead.last_inbound_at:
        lead.last_inbound_at = sent_at
        lead.service_window_expires_at = sent_at + SERVICE_WINDOW
    lead.last_activity_at = utcnow()

    text, media_type = extract_whatsapp_text(msg)
    message = Message(
        user_id=user_id,
        lead_id=lead.lead_id,
        channel=Channel.WHATSAPP,
        direction=Direction.INBOUND,
        sender=Sender.LEAD,
        text=text,
        media_type=media_type,
        platform_message_id=msg.get("id"),
        delivery_status=DeliveryStatus.RECEIVED,
        timestamp=sent_at,
        payload={"type": msg.get("type")},
    )
    session.add(message)
    session.flush()
    return CaptureResult(lead, created, message, text, media_type)


def _field_map(lead_data: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in lead_data.get("field_data") or []:
        values = item.get("values") or []
        if item.get("name") and values:
            out[str(item["name"]).casefold()] = str(values[0]).strip()
    return out


def _opted_in(fields: dict[str, str], lead_data: dict[str, Any], optin_field: str) -> bool:
    key = optin_field.casefold()
    if fields.get(key, "").casefold() in _TRUTHY:
        return True
    for resp in lead_data.get("custom_disclaimer_responses") or []:
        if str(resp.get("checkbox_key", "")).casefold() == key:
            return str(resp.get("is_checked", "")).casefold() in _TRUTHY
    return False


def capture_lead_form(session: Session, user_id: str, lead_data: dict[str, Any]) -> CaptureResult:
    settings = get_settings()
    fields = _field_map(lead_data)
    name = fields.get("full_name") or " ".join(
        x for x in (fields.get("first_name"), fields.get("last_name")) if x
    )
    phone = normalize_phone(fields.get("phone_number") or fields.get("phone"))
    meta_lead_id = str(lead_data.get("id", ""))

    lead = None
    created = False
    if meta_lead_id:
        lead = session.scalar(select(Lead).where(Lead.user_id == user_id, Lead.meta_lead_id == meta_lead_id))
    if lead is None and phone:
        lead, created = find_or_create_by_phone(session, user_id, phone)
    if lead is None:
        lead = Lead(user_id=user_id, preferred_channel=Channel.WHATSAPP, qualify_slots={})
        session.add(lead)
        created = True

    lead.name = lead.name or name or None
    lead.contact_email = lead.contact_email or fields.get("email")
    lead.meta_lead_id = meta_lead_id or lead.meta_lead_id
    lead.ad_id = str(lead_data["ad_id"]) if lead_data.get("ad_id") else lead.ad_id
    lead.campaign_id = str(lead_data["campaign_id"]) if lead_data.get("campaign_id") else lead.campaign_id
    lead.product_interest = fields.get(settings.leadform_product_field.casefold()) or lead.product_interest
    if lead.source is None:
        lead.source = LeadSource.AD_FORM
    if lead_data.get("form_id"):
        lead.source_detail = f"Lead form {lead_data['form_id']}"
    lead.last_activity_at = utcnow()
    session.flush()

    if _opted_in(fields, lead_data, settings.leadform_optin_field) and not lead.whatsapp_opt_in:
        now = utcnow()
        lead.whatsapp_opt_in = True
        lead.opt_in_source = OptInSource.LEAD_FORM
        lead.opt_in_at = now
        session.add(
            ConsentEvent(
                user_id=user_id,
                lead_id=lead.lead_id,
                type=ConsentType.OPT_IN,
                channel=Channel.WHATSAPP,
                source=OptInSource.LEAD_FORM,
                wording_shown=settings.leadform_optin_wording,
                timestamp=now,
            )
        )
    return CaptureResult(lead, created)


# --------------------------------------------------------------- Week 3: Instagram / Messenger

_ATTACHMENT_TYPES = {"image": "image", "audio": "audio", "video": "video", "file": "document"}


@dataclass(frozen=True)
class CommentInfo:
    channel: str  # instagram | messenger (Facebook Page comments are answered via Messenger)
    comment_id: str
    text: str
    author_id: str
    author_name: str | None
    post_id: str | None
    created_at: datetime
    account_id: str  # our Page / Instagram account ID (to skip our own comments)


def parse_comment(event_type: str, payload: dict[str, Any]) -> CommentInfo:
    c = payload["comment"]
    author = c.get("from") or {}
    if event_type == "instagram.comment":
        return CommentInfo(
            channel=Channel.INSTAGRAM,
            comment_id=str(c["id"]),
            text=c.get("text") or "",
            author_id=str(author.get("id", "")),
            author_name=author.get("username"),
            post_id=(c.get("media") or {}).get("id"),
            # The comment itself has no timestamp; Meta's delivery time for the entry is close.
            created_at=_entry_time(payload.get("entry_time")),
            account_id=str(payload.get("entry_id", "")),
        )
    return CommentInfo(
        channel=Channel.MESSENGER,
        comment_id=str(c["comment_id"]),
        text=c.get("message") or "",
        author_id=str(author.get("id", "")),
        author_name=author.get("name"),
        post_id=c.get("post_id"),
        created_at=_epoch(c.get("created_time")),
        account_id=str(payload.get("entry_id", "")),
    )


def _entry_time(value: Any) -> datetime:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return utcnow()
    return datetime.fromtimestamp(number / 1000 if number > 10**11 else number, UTC)


def _scoped_column(channel: str):  # noqa: ANN202
    return Lead.instagram_scoped_id if channel == Channel.INSTAGRAM else Lead.messenger_scoped_id


def find_or_create_by_scoped_id(
    session: Session, user_id: str, channel: str, scoped_id: str, source: str
) -> tuple[Lead, bool]:
    column = _scoped_column(channel)
    lead = session.scalar(
        select(Lead).where(Lead.user_id == user_id, column == scoped_id).order_by(Lead.created_at).limit(1)
    )
    if lead is not None:
        return lead, False
    lead = Lead(user_id=user_id, preferred_channel=channel, source=source, qualify_slots={})
    setattr(lead, column.key, scoped_id)
    session.add(lead)
    session.flush()
    return lead, True


def capture_comment(session: Session, user_id: str, info: CommentInfo) -> CaptureResult:
    """Create/find the commenter's lead and log the comment (media_type "comment")."""
    if info.channel == Channel.INSTAGRAM:
        # Instagram comment author IDs are the same IGSIDs that their DMs use.
        lead, created = find_or_create_by_scoped_id(session, user_id, info.channel, info.author_id, "comment")
    else:
        lead = session.scalar(
            select(Lead).where(Lead.user_id == user_id, Lead.facebook_user_id == info.author_id).limit(1)
        )
        created = lead is None
        if lead is None:
            lead = Lead(
                user_id=user_id,
                preferred_channel=Channel.MESSENGER,
                source=LeadSource.COMMENT,
                facebook_user_id=info.author_id,
                qualify_slots={},
            )
            session.add(lead)
            session.flush()
    if info.author_name and not lead.name:
        lead.name = info.author_name
    where = "Instagram" if info.channel == Channel.INSTAGRAM else "Facebook"
    if created or not lead.source_detail:
        lead.source_detail = f"{where} comment on post {info.post_id or '?'}"
    lead.last_activity_at = utcnow()
    message = Message(
        user_id=user_id,
        lead_id=lead.lead_id,
        channel=info.channel,
        direction=Direction.INBOUND,
        sender=Sender.LEAD,
        text=info.text,
        media_type="comment",  # not a DM: does not open the 24h messaging window
        platform_message_id=info.comment_id,
        delivery_status=DeliveryStatus.RECEIVED,
        timestamp=info.created_at,
        payload={"type": "comment", "post_id": info.post_id},
    )
    session.add(message)
    session.flush()
    return CaptureResult(lead, created, message, info.text, "none")


def capture_meta_message(
    session: Session, user_id: str, channel: str, payload: dict[str, Any]
) -> CaptureResult:
    """Instagram DM or Messenger message."""
    item = payload["messaging"]
    sender_id = str((item.get("sender") or {}).get("id", ""))
    if not sender_id:
        raise ValueError("message without sender id")
    lead, created = find_or_create_by_scoped_id(session, user_id, channel, sender_id, LeadSource.DM)

    msg = item.get("message") or {}
    postback = item.get("postback") or {}
    text = msg.get("text") or postback.get("title")
    media_type = "none"
    attachments = msg.get("attachments") or []
    if attachments and not text:
        media_type = _ATTACHMENT_TYPES.get(
            attachments[0].get("type"), str(attachments[0].get("type") or "media")
        )

    ts = item.get("timestamp")
    sent_at = datetime.fromtimestamp(int(ts) / 1000, UTC) if ts else utcnow()
    lead.preferred_channel = lead.preferred_channel or channel
    lead.last_activity_at = utcnow()
    message = Message(
        user_id=user_id,
        lead_id=lead.lead_id,
        channel=channel,
        direction=Direction.INBOUND,
        sender=Sender.LEAD,
        text=text,
        media_type=media_type,
        platform_message_id=msg.get("mid") or postback.get("mid"),
        delivery_status=DeliveryStatus.RECEIVED,
        timestamp=sent_at,
        payload={"type": "postback" if postback else "message"},
    )
    session.add(message)
    session.flush()
    return CaptureResult(lead, created, message, text, media_type)
