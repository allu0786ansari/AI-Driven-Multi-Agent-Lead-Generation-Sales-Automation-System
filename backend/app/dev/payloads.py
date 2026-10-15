"""Builders for realistic Meta webhook payloads.

Shapes follow Meta's documented webhook formats (WhatsApp Cloud API, Page
leadgen/feed, Messenger, Instagram comments and messaging). Used by the tests
and by `scripts/simulate.py`, so we can exercise every flow before Meta
approves the app. Default IDs are random so repeated calls are new events;
pass an ID explicitly to simulate a duplicate delivery.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

DEFAULT_PAGE_ID = "100000000000001"
DEFAULT_IG_ACCOUNT_ID = "17840000000000001"
DEFAULT_WABA_ID = "200000000000001"
DEFAULT_PHONE_NUMBER_ID = "300000000000001"
DEFAULT_DISPLAY_PHONE = "919000000001"


def _rid(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex


def _num_id(digits: int = 15) -> str:
    return str(uuid.uuid4().int)[:digits]


def _now() -> int:
    return int(time.time())


# ------------------------------------------------------------------ WhatsApp


def _wa_envelope(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": DEFAULT_WABA_ID,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": DEFAULT_DISPLAY_PHONE,
                                "phone_number_id": DEFAULT_PHONE_NUMBER_ID,
                            },
                            **value,
                        },
                    }
                ],
            }
        ],
    }


def whatsapp_text(
    from_phone: str = "919876543210",
    text: str = "Hi, what is the price of the air fryer?",
    name: str = "Test Customer",
    msg_id: str | None = None,
    referral: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "from": from_phone,
        "id": msg_id or _rid("wamid.TEST"),
        "timestamp": str(_now()),
        "type": "text",
        "text": {"body": text},
    }
    if referral:
        message["referral"] = referral
    return _wa_envelope(
        {"contacts": [{"profile": {"name": name}, "wa_id": from_phone}], "messages": [message]}
    )


def whatsapp_ctwa(
    from_phone: str = "919876543211",
    text: str = "Hello! Can I get more info on this?",
    name: str = "Ad Customer",
    ad_id: str = "120200000000000001",
    ctwa_clid: str | None = None,
    headline: str = "Air fryers from Rs 4,999",
    msg_id: str | None = None,
) -> dict[str, Any]:
    """Message that started from a Click-to-WhatsApp ad (carries a `referral` object)."""
    referral = {
        "source_url": "https://fb.me/test-ad",
        "source_id": ad_id,
        "source_type": "ad",
        "headline": headline,
        "body": "Tap to chat with us on WhatsApp",
        "media_type": "image",
        "image_url": "https://example.com/ad.jpg",
        "ctwa_clid": ctwa_clid or _rid("ARAk"),
    }
    return whatsapp_text(from_phone, text, name, msg_id, referral)


def whatsapp_audio(
    from_phone: str = "919876543210", msg_id: str | None = None, name: str = "Voice Customer"
) -> dict[str, Any]:
    return _wa_envelope(
        {
            "contacts": [{"profile": {"name": name}, "wa_id": from_phone}],
            "messages": [
                {
                    "from": from_phone,
                    "id": msg_id or _rid("wamid.TEST"),
                    "timestamp": str(_now()),
                    "type": "audio",
                    "audio": {"id": _num_id(), "mime_type": "audio/ogg; codecs=opus", "voice": True},
                }
            ],
        }
    )


def whatsapp_status(
    msg_id: str,
    status: str = "delivered",
    recipient: str = "919876543210",
    pricing_category: str | None = None,
) -> dict[str, Any]:
    """Delivery receipt. Pass `pricing_category` to include Meta's billing info (omitted by default,
    so a simulated receipt never relabels how a message was billed)."""
    entry: dict[str, Any] = {
        "id": msg_id,
        "status": status,
        "timestamp": str(_now()),
        "recipient_id": recipient,
    }
    if pricing_category:
        entry["pricing"] = {"billable": True, "pricing_model": "PMP", "category": pricing_category}
    if status == "failed":
        entry["errors"] = [
            {"code": 131047, "title": "Re-engagement message", "message": "More than 24 hours have passed"}
        ]
    return _wa_envelope({"statuses": [entry]})


# ---------------------------------------------------------------------- Page


def leadgen(
    leadgen_id: str | None = None,
    form_id: str = "400000000000001",
    ad_id: str = "120200000000000002",
    page_id: str = DEFAULT_PAGE_ID,
) -> dict[str, Any]:
    """Lead form submission. Meta sends only IDs; the fields are fetched via the Graph API."""
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": _now(),
                "changes": [
                    {
                        "field": "leadgen",
                        "value": {
                            "ad_id": ad_id,
                            "form_id": form_id,
                            "leadgen_id": leadgen_id or _num_id(),
                            "created_time": _now(),
                            "page_id": page_id,
                            "adgroup_id": ad_id,
                        },
                    }
                ],
            }
        ],
    }


def facebook_comment(
    text: str = "Price please?",
    comment_id: str | None = None,
    post_id: str | None = None,
    from_id: str = "500000000000001",
    from_name: str = "FB Commenter",
    page_id: str = DEFAULT_PAGE_ID,
    verb: str = "add",
) -> dict[str, Any]:
    post_id = post_id or f"{page_id}_{_num_id()}"
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": _now(),
                "changes": [
                    {
                        "field": "feed",
                        "value": {
                            "from": {"id": from_id, "name": from_name},
                            "post_id": post_id,
                            "comment_id": comment_id or f"{post_id.split('_')[-1]}_{_num_id()}",
                            "parent_id": post_id,
                            "created_time": _now(),
                            "item": "comment",
                            "verb": verb,
                            "message": text,
                        },
                    }
                ],
            }
        ],
    }


def facebook_reaction(page_id: str = DEFAULT_PAGE_ID) -> dict[str, Any]:
    """A 'like' on a post - recognized but not actionable (skipped)."""
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": _now(),
                "changes": [
                    {
                        "field": "feed",
                        "value": {
                            "item": "reaction",
                            "verb": "add",
                            "reaction_type": "like",
                            "post_id": f"{page_id}_{_num_id()}",
                            "from": {"id": _num_id(), "name": "Someone"},
                        },
                    }
                ],
            }
        ],
    }


def _messaging_envelope(obj: str, account_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return {"object": obj, "entry": [{"id": account_id, "time": _now() * 1000, "messaging": [item]}]}


def messenger_message(
    text: str = "Do you deliver to Chennai?",
    psid: str = "600000000000001",
    mid: str | None = None,
    page_id: str = DEFAULT_PAGE_ID,
    is_echo: bool = False,
) -> dict[str, Any]:
    message: dict[str, Any] = {"mid": mid or _rid("m_"), "text": text}
    if is_echo:
        message["is_echo"] = True
    sender, recipient = (page_id, psid) if is_echo else (psid, page_id)
    return _messaging_envelope(
        "page",
        page_id,
        {
            "sender": {"id": sender},
            "recipient": {"id": recipient},
            "timestamp": _now() * 1000,
            "message": message,
        },
    )


# ----------------------------------------------------------------- Instagram


def instagram_comment(
    text: str = "How much is this? 😍",
    comment_id: str | None = None,
    media_id: str | None = None,
    from_id: str = "700000000000001",
    username: str = "insta_shopper",
    ig_account_id: str = DEFAULT_IG_ACCOUNT_ID,
    live: bool = False,
) -> dict[str, Any]:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": ig_account_id,
                "time": _now(),
                "changes": [
                    {
                        "field": "live_comments" if live else "comments",
                        "value": {
                            "id": comment_id or _num_id(17),
                            "text": text,
                            "from": {"id": from_id, "username": username},
                            "media": {"id": media_id or _num_id(17), "media_product_type": "FEED"},
                        },
                    }
                ],
            }
        ],
    }


def instagram_message(
    text: str = "Yes, I want the 6L one. Is it available?",
    igsid: str = "800000000000001",
    mid: str | None = None,
    ig_account_id: str = DEFAULT_IG_ACCOUNT_ID,
    is_echo: bool = False,
) -> dict[str, Any]:
    message: dict[str, Any] = {"mid": mid or _rid("aWdf"), "text": text}
    if is_echo:
        message["is_echo"] = True
    sender, recipient = (ig_account_id, igsid) if is_echo else (igsid, ig_account_id)
    return _messaging_envelope(
        "instagram",
        ig_account_id,
        {
            "sender": {"id": sender},
            "recipient": {"id": recipient},
            "timestamp": _now() * 1000,
            "message": message,
        },
    )


def instagram_read_receipt(
    igsid: str = "800000000000001", ig_account_id: str = DEFAULT_IG_ACCOUNT_ID
) -> dict[str, Any]:
    return _messaging_envelope(
        "instagram",
        ig_account_id,
        {
            "sender": {"id": igsid},
            "recipient": {"id": ig_account_id},
            "timestamp": _now() * 1000,
            "read": {"mid": _rid("aWdf")},
        },
    )


# Which endpoint each payload type belongs to (used by the simulator).
ENDPOINT_FOR_OBJECT = {
    "whatsapp_business_account": "/webhooks/whatsapp",
    "page": "/webhooks/meta/page",
    "instagram": "/webhooks/meta/instagram",
}
