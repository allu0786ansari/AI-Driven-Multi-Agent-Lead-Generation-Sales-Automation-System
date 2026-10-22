"""Allowed values for status-like columns (stored as plain strings in the database)."""

from enum import StrEnum


class LeadStatus(StrEnum):
    NEW = "new"
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    CONTACTED = "contacted"
    CONVERTED = "converted"
    LOST = "lost"


class LeadSource(StrEnum):
    CTWA_AD = "ctwa_ad"
    AD_FORM = "ad_form"
    COMMENT = "comment"
    DM = "dm"
    WEBSITE_CHAT = "website_chat"


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    MESSENGER = "messenger"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class Sender(StrEnum):
    AGENT = "agent"
    LEAD = "lead"
    SALESPERSON = "salesperson"
    SYSTEM = "system"  # automatic holding / confirmation messages (not subject to takeover)
    NURTURE = "nurture"  # scheduled follow-up templates (Week 5); paused by takeover like the agent


class PricingCategory(StrEnum):
    MARKETING = "marketing"
    UTILITY = "utility"
    SERVICE = "service"
    FREE_ENTRY = "free_entry"
    FREE = "free"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    RECEIVED = "received"  # inbound message
    QUEUED = "queued"  # outbound, waiting in the outbox
    SENDING = "sending"  # outbound, API call in progress
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


# Status webhooks can arrive out of order; never move a message backwards.
DELIVERY_RANK = {
    DeliveryStatus.QUEUED: 0,
    DeliveryStatus.SENDING: 1,
    DeliveryStatus.SENT: 2,
    DeliveryStatus.DELIVERED: 3,
    DeliveryStatus.READ: 4,
}


class ConsentType(StrEnum):
    OPT_IN = "opt_in"
    OPT_OUT = "opt_out"


class OptInSource(StrEnum):
    LEAD_FORM = "lead_form"
    CTWA_MESSAGE = "ctwa_message"
    DM = "dm"
    MANUAL = "manual"
    KEYWORD = "keyword"  # the customer sent START after opting out (Week 5)


class StockStatus(StrEnum):
    IN_STOCK = "in_stock"
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"


class EventStatus(StrEnum):
    """Lifecycle of a stored inbound webhook event."""

    PENDING = "pending"  # stored, waiting for the worker
    PROCESSING = "processing"  # claimed by the worker
    PROCESSED = "processed"  # a handler ran successfully
    IGNORED = "ignored"  # no handler for this event type (yet), or nothing to do
    RETRY = "retry"  # handler failed, will be retried after next_attempt_at
    FAILED = "failed"  # gave up after max attempts


class EventType(StrEnum):
    """Normalized event types produced by webhook ingestion (see app/webhooks/parse.py)."""

    WHATSAPP_MESSAGE = "whatsapp.message"
    WHATSAPP_STATUS = "whatsapp.status"
    PAGE_LEADGEN = "page.leadgen"
    PAGE_COMMENT = "page.comment"
    PAGE_MESSAGE = "page.message"  # Messenger
    PAGE_MESSAGE_ECHO = "page.message_echo"
    INSTAGRAM_COMMENT = "instagram.comment"
    INSTAGRAM_MESSAGE = "instagram.message"
    INSTAGRAM_MESSAGE_ECHO = "instagram.message_echo"
    UNKNOWN = "unknown"


class NotificationKind(StrEnum):
    HOT_LEAD = "hot_lead"
    HANDOFF = "handoff"  # the AI handed the conversation to a person
    CUSTOMER_REPLIED = "customer_replied"  # a lead under human takeover wrote again
    MESSAGE_FAILED = "message_failed"  # a message to the customer could not be delivered (Week 6)


class NotificationStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"  # delivered to at least one channel
    LOGGED = "logged"  # ALERTS_MODE=log: written to the log only
    FAILED = "failed"


class CommentIntent(StrEnum):
    PURCHASE_INTEREST = "purchase_interest"
    PRODUCT_QUESTION = "product_question"
    COMPLAINT = "complaint"
    SPAM = "spam"
    OTHER = "other"
