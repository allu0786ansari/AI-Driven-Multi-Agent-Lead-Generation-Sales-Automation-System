"""Pull simple structured filters out of a customer's message.

"air fryer under 5000", "5k tak ka mixer", "budget 3,500" -> max price.
English and common Hinglish phrasings; anything not recognized just means no
filter (the vector search still runs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

_NUM = r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|hazaar|hazar)?"
_MAX_BEFORE = re.compile(
    r"\b(?:under|below|less than|within|upto|up to|max(?:imum)?|budget(?: is| of)?|not more than)\s*" + _NUM,
    re.IGNORECASE,
)
_MAX_AFTER = re.compile(_NUM + r"\s*(?:tak|se kam|ke andar|ke under|or less|max|budget)\b", re.IGNORECASE)
_MIN_BEFORE = re.compile(r"\b(?:above|over|more than|at least|minimum)\s*" + _NUM, re.IGNORECASE)


@dataclass(frozen=True)
class ProductFilters:
    category: str | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    in_stock_only: bool = False


def _to_amount(number: str, suffix: str | None) -> Decimal | None:
    try:
        value = Decimal(number.replace(",", ""))
    except ArithmeticError:
        return None
    if suffix:
        value *= 1000
    # Ignore tiny numbers ("under 2 years warranty") - not a budget.
    return value if value >= 100 else None


def parse_price_constraints(text: str) -> ProductFilters:
    max_price = min_price = None
    for pattern in (_MAX_BEFORE, _MAX_AFTER):
        m = pattern.search(text)
        if m:
            max_price = _to_amount(m.group(1), m.group(2))
            if max_price is not None:
                break
    m = _MIN_BEFORE.search(text)
    if m:
        min_price = _to_amount(m.group(1), m.group(2))
    return ProductFilters(min_price=min_price, max_price=max_price)
