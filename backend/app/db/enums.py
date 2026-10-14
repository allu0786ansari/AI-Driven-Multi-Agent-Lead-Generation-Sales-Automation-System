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


class PricingCategory(StrEnum):
    MARKETING = "marketing"
    UTILITY = "utility"
    SERVICE = "service"
    FREE_ENTRY = "free_entry"
    FREE = "free"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class ConsentType(StrEnum):
    OPT_IN = "opt_in"
    OPT_OUT = "opt_out"


class OptInSource(StrEnum):
    LEAD_FORM = "lead_form"
    CTWA_MESSAGE = "ctwa_message"
    DM = "dm"
    MANUAL = "manual"


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
