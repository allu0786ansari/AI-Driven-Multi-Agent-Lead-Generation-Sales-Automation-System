"""Plain snapshots of products, safe to use after the database session closes."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.db.enums import StockStatus
from app.db.models import Product

STOCK_PHRASES = {
    StockStatus.IN_STOCK: "in stock",
    StockStatus.LOW_STOCK: "in stock, only a few left",
    StockStatus.OUT_OF_STOCK: "currently out of stock",
    StockStatus.PREORDER: "available for pre-order",
}


@dataclass(frozen=True)
class ProductRecord:
    product_id: str
    sku: str
    name: str
    category: str
    price: Decimal
    currency: str
    description: str
    key_attributes: dict[str, Any] = field(default_factory=dict)
    stock_status: str = StockStatus.IN_STOCK

    @classmethod
    def from_row(cls, p: Product) -> ProductRecord:
        return cls(
            product_id=p.product_id,
            sku=p.sku,
            name=p.name,
            category=p.category,
            price=Decimal(str(p.price)).quantize(Decimal("0.01")),
            currency=p.currency,
            description=p.description or "",
            key_attributes=dict(p.key_attributes or {}),
            stock_status=p.stock_status,
        )

    @property
    def stock_phrase(self) -> str:
        return STOCK_PHRASES.get(StockStatus(self.stock_status), self.stock_status)

    @property
    def searchable_text(self) -> str:
        attrs = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in self.key_attributes.items())
        return f"{self.name}. Category: {self.category}. {self.description} {attrs}".strip()


def format_price(amount: Decimal, currency: str = "INR") -> str:
    """Indian digit grouping: 112999 -> Rs 1,12,999. Drops .00 for whole rupees."""
    amount = Decimal(amount).quantize(Decimal("0.01"))
    whole, frac = divmod(amount, 1)
    digits = str(int(whole))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    text = digits if frac == 0 else f"{digits}.{int(frac * 100):02d}"
    symbol = "₹" if currency == "INR" else f"{currency} "
    return f"{symbol}{text}"
