"""Lead scoring rubric (spec 3.3: pure function, no LLM).

Points come from the business's QualificationConfig; the total is capped at
100 and mapped to hot / warm / cold. Every contribution is listed in the
rationale so the salesperson can see why a lead is hot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.qualify.config import QualificationConfig
from app.qualify.slots import QualifySlots
from app.rag.records import format_price


@dataclass(frozen=True)
class ScoreResult:
    score: int
    status: str  # hot | warm | cold
    reasons: list[str] = field(default_factory=list)

    @property
    def rationale(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "not enough information yet"


def score(
    slots: QualifySlots,
    config: QualificationConfig,
    inbound_messages: int = 1,
    reference_price: Decimal | None = None,
) -> ScoreResult:
    w = config.weights
    points = 0
    reasons: list[str] = []

    def add(value: int, reason: str) -> None:
        nonlocal points
        points += value
        reasons.append(f"{reason} ({value:+d})")

    if slots.product or slots.category:
        add(w.product, f"wants {slots.product or slots.category}")
    if slots.budget is not None:
        add(w.budget_known, f"budget {format_price(slots.budget)}")
        if reference_price is not None and slots.budget >= reference_price:
            add(w.budget_fits, "budget covers the product")
    if slots.timeline in ("now", "this_week"):
        add(w.timeline_soon, f"buying {slots.timeline.replace('_', ' ')}")
    elif slots.timeline == "this_month":
        add(w.timeline_this_month, "buying this month")
    elif slots.timeline == "later":
        add(w.timeline_later, "buying later")
    if slots.quantity:
        add(w.quantity_known, f"quantity {slots.quantity}")
        if slots.quantity >= config.bulk_threshold:
            add(w.bulk, "bulk order")
    if slots.city:
        add(w.city, f"delivery to {slots.city}")
    if slots.purchase_intent == "high":
        add(w.intent_high, "ready to buy")
    elif slots.purchase_intent == "medium":
        add(w.intent_medium, "asking about price/availability")
    if inbound_messages >= 2:
        add(w.engaged, f"{inbound_messages} messages")
    if slots.sentiment == "negative":
        add(w.negative_sentiment, "unhappy")

    total = max(0, min(100, points))
    status = "hot" if total >= config.hot_threshold else "warm" if total >= config.warm_threshold else "cold"
    return ScoreResult(total, status, reasons)


def summarize(slots: QualifySlots) -> str:
    """Short factual summary for the lead list and the Sheets mirror (no LLM, no guesswork)."""
    parts = []
    if slots.product or slots.category:
        parts.append(f"Wants: {slots.product or slots.category}")
    if slots.quantity:
        parts.append(f"Qty {slots.quantity}")
    if slots.budget is not None:
        parts.append(f"Budget {format_price(slots.budget)}")
    if slots.timeline:
        parts.append(f"Timeline: {slots.timeline.replace('_', ' ')}")
    if slots.city:
        parts.append(f"City: {slots.city}")
    if slots.sentiment == "negative":
        parts.append("Unhappy")
    if slots.wants_human:
        parts.append("Asked for a person")
    return " · ".join(parts) or "No details yet"


def next_question(slots: QualifySlots, config: QualificationConfig) -> str | None:
    """The first configured question whose answer we don't have yet."""
    known = {
        "product": bool(slots.product or slots.category),
        "quantity": slots.quantity is not None,
        "timeline": slots.timeline is not None,
        "city": slots.city is not None,
        "budget": slots.budget is not None,
    }
    for q in config.questions:
        if not known[q.slot]:
            return q.question
    return None
