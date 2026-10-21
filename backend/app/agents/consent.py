"""Consent in the chat: opt-out words, opting back in, and the one-time opt-in question.

Rules (docs/DECISIONS.md D-030, D-031):
- An opt-out word or the template's "Stop messages" button stops every message
  the business starts (follow-ups, templates) at once.
- If the customer writes to us again later, we answer that conversation: they
  started it. Follow-ups stay off until they send START.
- WhatsApp customers who came from an ad or wrote to us first have not agreed
  to offers yet. The assistant asks once ("Can we send you offers and
  reminders on WhatsApp? Reply YES or NO."); a YES is recorded with the exact
  wording, like a lead-form checkbox.

Matching works on the whole message after removing punctuation, emoji and
polite filler words, so "STOP!!", "stop please" and "बंद करो" opt out, but
"cancel my order" or "stop, I want the 6L one" do not.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.enums import Channel, ConsentType, LeadSource, OptInSource
from app.db.models import ConsentEvent, Lead

OPT_IN_QUESTION = "Can we send you offers and reminders on WhatsApp? Reply YES or NO."
OPT_OUT_CONFIRMATION = (
    "You won't receive any more offers or reminders from us. If you have a question later, "
    "just message us here. To get offers again, send START."
)
OPT_BACK_IN_CONFIRMATION = (
    "Welcome back! We'll send you offers and reminders now and then. Send STOP anytime."
)
OPT_IN_YES_REPLY = (
    "Thank you! We'll send you offers and reminders now and then. Send STOP anytime to stop them."
)
OPT_IN_NO_REPLY = "No problem, we won't send you offers. Ask me anything about our products anytime."

# Whole message (after fillers are removed) means "stop messaging me".
_OPT_OUT_EXACT = {
    # English, including WhatsApp template quick-reply buttons
    "stop", "stop messages", "stop message", "stop msgs", "stop promotions", "stop promotion",
    "stop offers", "stop all", "unsubscribe", "cancel", "opt out", "optout", "opt-out",
    "no more messages", "no more msgs", "remove me", "remove my number", "dont message me",
    "do not message me", "dont send messages", "do not send messages", "stop sending messages",
    "stop sending me messages", "stop messaging me", "stop messaging", "dont contact me",
    "do not contact me",
    # Hindi / Hinglish
    "band", "band karo", "band kardo", "band kar do", "bandh", "bandh karo", "bandh kar do",
    "mat bhejo", "message mat bhejo", "msg mat bhejo", "messages mat bhejo", "message band karo",
    "messages band karo", "बंद", "बंद करो", "बंद कर दो", "बंद करें", "मत भेजो", "मैसेज मत भेजो",
    "संदेश मत भेजो", "मैसेज बंद करो",
    # Tamil / Tanglish
    "niruthu", "niruthunga", "nirutthu", "message anupa vendam", "anupa vendam", "anuppa vendam",
    "message venda", "message vendam", "நிறுத்து", "நிறுத்துங்கள்", "நிறுத்தவும்",
    "அனுப்ப வேண்டாம்", "மெசேஜ் அனுப்ப வேண்டாம்",
}  # fmt: skip

_OPT_IN_AGAIN = {"start", "subscribe", "resume", "start messages", "unstop", "shuru karo", "शुरू करो", "शुरू"}

_YES = {
    "yes", "y", "yeah", "yep", "yes please", "ok", "okay", "ok sure", "sure", "fine", "send",
    "haan", "han", "haa", "ha", "haan ji", "ji haan", "theek hai", "thik hai", "ok bhejo",
    "aama", "aamaa", "sari", "seri", "ok anupunga",
    "हाँ", "हां", "हाँ जी", "ठीक है", "ஆமா", "ஆம்", "சரி",
}  # fmt: skip
_NO = {
    "no", "n", "nope", "no thanks", "no thank you", "not now", "nahi", "nahin", "na", "nako",
    "illa", "illai", "venda", "vendam", "नहीं", "ना", "இல்லை", "வேண்டாம்",
}  # fmt: skip

_FILLERS = {
    "please",
    "pls",
    "plz",
    "ji",
    "sir",
    "madam",
    "mam",
    "bhai",
    "anna",
    "thanks",
    "thank",
    "you",
    "now",
}


def normalize(text: str | None) -> str:
    """Lower-case, no punctuation/emoji, single spaces. Keeps Hindi/Tamil vowel signs.

    (Python's \\w would drop combining marks such as the anusvara in "बंद".)
    """
    if not text:
        return ""
    kept = []
    for ch in text.casefold():
        cat = unicodedata.category(ch)
        if ch == "'" or ch == "’":
            continue  # "don't" -> "dont"
        kept.append(" " if cat[0] in "PSZC" else ch)
    return " ".join("".join(kept).split())


def _core(text: str | None) -> str:
    words = normalize(text).split()
    core = [w for w in words if w not in _FILLERS]
    return " ".join(core or words)


# Short messages about *our messages* that start with a stop verb also opt out:
# "stop sending these offers", "unsubscribe me", "no more promotions please".
# Anything about an order or a product never does ("stop the order", "cancel my order").
_STOP_START = ("unsubscribe", "stop", "dont send", "do not send", "no more", "band karo", "mat bhejo")
_ABOUT_MESSAGES = {
    "message", "messages", "msg", "msgs", "offer", "offers", "promotion", "promotions", "promo", "promos",
    "ads", "advertisement", "advertisements", "updates", "notifications", "texts", "spam",
}  # fmt: skip
# Not "me" / "this": "don't send me the 4L, send the 6L" must stay a normal message.
_ABOUT_ORDERS = {"order", "orders", "delivery", "payment", "booking", "product", "item"}
_MAX_WORDS = 7


def is_opt_out(text: str | None) -> bool:
    core = _core(text)
    if core in _OPT_OUT_EXACT or normalize(text) in _OPT_OUT_EXACT:
        return True
    words = core.split()
    if not words or len(words) > _MAX_WORDS or _ABOUT_ORDERS & set(words):
        return False
    if words[0] == "unsubscribe":
        return True
    starts = any(core == s or core.startswith(s + " ") for s in _STOP_START)
    return starts and bool(_ABOUT_MESSAGES & set(words[1:]))


def is_opt_back_in(text: str | None) -> bool:
    return _core(text) in _OPT_IN_AGAIN


def yes_or_no(text: str | None) -> bool | None:
    core = normalize(text)  # keep "thank you" etc. as part of the answer
    if core in _YES or _core(text) in _YES:
        return True
    if core in _NO or _core(text) in _NO:
        return False
    return None


def record_opt_out(session: Session, lead: Lead, source: str, channel: str = Channel.WHATSAPP) -> None:
    now = utcnow()
    lead.opted_out = True
    lead.opted_out_at = now
    lead.nurture_next_at = None
    session.add(
        ConsentEvent(
            user_id=lead.user_id, lead_id=lead.lead_id, type=ConsentType.OPT_OUT,
            channel=channel, source=source, timestamp=now,
        )
    )  # fmt: skip


def record_opt_in(
    session: Session, lead: Lead, source: str, wording: str | None, channel: str = Channel.WHATSAPP
) -> None:
    now = utcnow()
    lead.opted_out = False
    lead.opted_out_at = None
    if channel == Channel.WHATSAPP:
        lead.whatsapp_opt_in = True
        lead.opt_in_source = source
        lead.opt_in_at = now
    lead.nurture_step = 0
    lead.nurture_next_at = None
    session.add(
        ConsentEvent(
            user_id=lead.user_id, lead_id=lead.lead_id, type=ConsentType.OPT_IN,
            channel=channel, source=source, wording_shown=wording, timestamp=now,
        )
    )  # fmt: skip


def chat_opt_in_source(lead: Lead) -> str:
    return OptInSource.CTWA_MESSAGE if lead.source == LeadSource.CTWA_AD else OptInSource.DM


def may_ask_opt_in(lead: Lead, channel: str) -> bool:
    """Ask once, on WhatsApp, if the customer has not agreed (or refused) yet."""
    return (
        channel == Channel.WHATSAPP
        and bool(lead.contact_phone)
        and not lead.whatsapp_opt_in
        and not lead.opted_out
        and lead.opt_in_asked_at is None
        and lead.erased_at is None
    )
