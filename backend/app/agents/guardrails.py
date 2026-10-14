"""Reply guardrails (spec Section 10).

1. The LLM writes {{price:SKU}} / {{stock:SKU}} / {{name:SKU}} placeholders;
   `render_placeholders` fills them from the catalog record, so prices and
   stock levels in replies always come from data, never from the model.
2. `validate_reply` checks the final text: every money amount must be a real
   price of a product in context, every other number must appear in the
   product data or the customer's message, and offers/discounts/delivery
   promises must be backed by the catalog text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.rag.embeddings import normalize_text
from app.rag.records import ProductRecord, format_price

_PLACEHOLDER = re.compile(r"\{\{\s*(price|stock|name)\s*:\s*([^}]+?)\s*\}\}", re.IGNORECASE)
_MONEY_BEFORE = re.compile(r"(?:₹|\brs\.?|\binr|\brupees?)\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_MONEY_AFTER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:/-|rupees?\b|rs\b|inr\b)", re.IGNORECASE)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

# Claims the model must not make unless the catalog text says so.
_PROMISE_WORDS = (
    "discount",
    "special offer",
    "limited offer",
    "cashback",
    "coupon",
    "free delivery",
    "free shipping",
    "emi",
    "% off",
    "sale",
    "guarantee",
)

SMALL_NUMBER_LIMIT = 12  # "1", "2 options", "24 hours" style numbers are harmless


@dataclass
class ValidationResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def _num(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "")).normalize()
    except (InvalidOperation, ValueError):
        return None


def render_placeholders(text: str, products: list[ProductRecord]) -> tuple[str, list[str]]:
    """Fill placeholders. Returns (rendered text, unknown SKUs referenced)."""
    by_sku = {p.sku.casefold(): p for p in products}
    unknown: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        kind, sku = m.group(1).lower(), m.group(2).strip()
        p = by_sku.get(sku.casefold())
        if p is None:
            unknown.append(sku)
            return m.group(0)
        if kind == "price":
            return format_price(p.price, p.currency)
        if kind == "stock":
            return p.stock_phrase
        return p.name

    return _PLACEHOLDER.sub(_sub, text), unknown


def _context_text(products: list[ProductRecord]) -> str:
    parts = []
    for p in products:
        parts.append(p.searchable_text)
        parts.append(str(p.price))
    return " ".join(parts)


def validate_reply(text: str, products: list[ProductRecord], customer_text: str = "") -> ValidationResult:
    problems: list[str] = []
    if "{{" in text or "}}" in text:
        problems.append("unfilled placeholder")

    prices = {_num(str(p.price)) for p in products}
    context = _context_text(products)
    context_norm = normalize_text(context)
    allowed_numbers = {_num(n) for n in _NUMBER.findall(context)} | {
        _num(n) for n in _NUMBER.findall(customer_text)
    }

    money_spans: list[tuple[int, int]] = []
    for pattern in (_MONEY_BEFORE, _MONEY_AFTER):
        for m in pattern.finditer(text):
            money_spans.append(m.span(1))
            amount = _num(m.group(1))
            if amount not in prices:
                problems.append(f"price {m.group(0).strip()!r} is not a catalog price")

    for m in _PERCENT.finditer(text):
        if _num(m.group(1)) not in allowed_numbers:
            problems.append(f"percentage {m.group(0)!r} not in product data")

    for m in _NUMBER.finditer(text):
        if any(start <= m.start() < end for start, end in money_spans):
            continue
        value = _num(m.group(0))
        if value is None or value in allowed_numbers or value <= SMALL_NUMBER_LIMIT:
            continue
        problems.append(f"number {m.group(0)!r} not in product data")

    text_norm = normalize_text(text)
    for word in _PROMISE_WORDS:
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text_norm) and word not in context_norm:
            problems.append(f"unsupported claim {word!r}")

    return ValidationResult(ok=not problems, problems=sorted(set(problems)))
