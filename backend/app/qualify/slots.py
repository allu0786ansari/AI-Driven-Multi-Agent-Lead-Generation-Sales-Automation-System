"""What we know about a lead's purchase, and rule-based extraction from a message.

The LLM also extracts these fields as part of its reply (one call per
message). The rules below run on every message too, so qualification keeps
working when the LLM is down or using the fake provider, and they cover the
common English / Hinglish / Tanglish phrasings.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.rag.query import parse_price_constraints

Timeline = Literal["now", "this_week", "this_month", "later"]
Intent = Literal["high", "medium", "low"]
Sentiment = Literal["positive", "neutral", "negative"]


class QualifySlots(BaseModel):
    """Stored in leads.qualify_slots (JSON)."""

    product: str | None = Field(default=None, description="Product name or SKU the customer wants")
    category: str | None = Field(default=None, description="Product category")
    quantity: int | None = Field(default=None, ge=1, le=10000, description="How many they want")
    budget: Decimal | None = Field(default=None, ge=0, description="Budget in rupees")
    timeline: Timeline | None = Field(default=None, description="When they plan to buy")
    city: str | None = Field(default=None, description="Delivery city or pincode")
    purchase_intent: Intent | None = Field(default=None, description="How ready they are to buy")
    sentiment: Sentiment | None = None
    wants_human: bool = Field(default=False, description="Asked to talk to a person")

    def merged_with(self, newer: QualifySlots) -> QualifySlots:
        """Newer non-empty values win (customers change their minds)."""
        data = self.model_dump()
        for key, value in newer.model_dump().items():
            if key == "wants_human":
                data[key] = data[key] or value
            elif value is not None:
                data[key] = value
        return QualifySlots.model_validate(data)


_QTY = [
    re.compile(r"\b(\d{1,4})\s*(?:pcs|pc|pieces?|units?|nos|items?|sets?|qty|chahiye|venum|vendum)\b", re.I),
    re.compile(r"\b(?:qty|quantity)\s*[:=-]?\s*(\d{1,4})\b", re.I),
]
# "later" is checked first so "not now" is not read as "now".
_TIMELINE: list[tuple[Timeline, re.Pattern[str]]] = [
    ("later", re.compile(r"\b(next month|later|baad mein|not now|just checking|just browsing|after diwali|next year|apram)\b", re.I)),
    ("now", re.compile(r"\b(today|now|right now|urgent(?:ly)?|immediately|asap|abhi|aaj|ippo|ippove|inniku|indru)\b", re.I)),
    ("this_week", re.compile(r"\b(this week|within a week|in a few days|few days|tomorrow|kal|is hafte|is week|naalaiku|naalai|weekend)\b", re.I)),
    ("this_month", re.compile(r"\b(this month|next week|is mahine|within a month|month end|end of the month)\b", re.I)),
]  # fmt: skip
_CITIES = [
    "chennai", "mumbai", "delhi", "new delhi", "bangalore", "bengaluru", "hyderabad", "kolkata", "pune",
    "ahmedabad", "jaipur", "lucknow", "coimbatore", "madurai", "trichy", "tiruchirappalli", "salem",
    "kochi", "cochin", "trivandrum", "thiruvananthapuram", "mysore", "mysuru", "mangalore", "surat",
    "indore", "nagpur", "chandigarh", "noida", "gurgaon", "gurugram", "thane", "navi mumbai", "vizag",
    "visakhapatnam", "vijayawada", "bhopal", "patna", "bhubaneswar", "guwahati", "kanpur", "vadodara",
    "nashik", "ludhiana", "agra", "varanasi", "tirupur", "erode", "vellore", "pondicherry", "puducherry",
    "chengalpattu", "kanchipuram", "tambaram",
]  # fmt: skip
_CITY_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in sorted(_CITIES, key=len, reverse=True)) + r")\b", re.I
)
_PINCODE = re.compile(r"\b([1-9]\d{5})\b")
_HIGH_INTENT = re.compile(
    r"\b(buy|order|purchase|book (?:it|one)|confirm|payment|pay now|cod|cash on delivery|lena hai|le lunga|"
    r"le lungi|kharidna|khareed|vaanganum|vaangu|order pannu|send (?:the )?payment link)\b",
    re.I,
)
_MEDIUM_INTENT = re.compile(
    r"\b(interested|price|cost|rate|available|availability|details|kitna|kitne|evlo|evvalavu|chahiye|venum|"
    r"stock|delivery)\b",
    re.I,
)
_NEGATIVE = re.compile(
    r"\b(worst|bad|broken|damaged|refund|complaint|fraud|cheat(?:ed|ing)?|angry|disappointed|not working|"
    r"kharab|bekar|bakwas|waste|useless|late delivery|never arrived)\b",
    re.I,
)
_WANTS_HUMAN = re.compile(
    r"\b(talk to (?:a |an )?(?:person|human|agent|someone|executive|manager)|call me|give me a call|phone call|"
    r"customer care|real person|insaan se|baat karni|baat karna|call karo|call pannunga|human please)\b",
    re.I,
)


def extract_rules(text: str) -> QualifySlots:
    slots: dict = {}
    budget = parse_price_constraints(text).max_price
    if budget is None:
        m = re.search(r"\bbudget\b[^\d₹]{0,15}(?:₹|rs\.?|inr)?\s*(\d[\d,]*)\s*(k)?\b", text, re.I)
        if m:
            value = Decimal(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
            budget = value if value >= 100 else None
    if budget is not None:
        slots["budget"] = budget
    for pattern in _QTY:
        m = pattern.search(text)
        if m and int(m.group(1)) >= 1:
            slots["quantity"] = int(m.group(1))
            break
    for timeline, pattern in _TIMELINE:
        if pattern.search(text):
            slots["timeline"] = timeline
            break
    m = _CITY_RE.search(text)
    if m:
        slots["city"] = m.group(1).title()
    else:
        pin = _PINCODE.search(text)
        if pin:
            slots["city"] = f"PIN {pin.group(1)}"
    if _HIGH_INTENT.search(text):
        slots["purchase_intent"] = "high"
    elif _MEDIUM_INTENT.search(text):
        slots["purchase_intent"] = "medium"
    if _NEGATIVE.search(text):
        slots["sentiment"] = "negative"
    slots["wants_human"] = bool(_WANTS_HUMAN.search(text))
    return QualifySlots.model_validate(slots)


def wants_human(text: str | None) -> bool:
    return bool(text and _WANTS_HUMAN.search(text))
