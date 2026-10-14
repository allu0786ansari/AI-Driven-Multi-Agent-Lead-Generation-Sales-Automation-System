"""Product catalog: validate a JSON catalog file and import it into the database.

File format (see data/catalog.sample.json):

    {"products": [{"sku": "...", "name": "...", "category": "...",
                   "price": 1999, "currency": "INR", "description": "...",
                   "key_attributes": {...}, "stock_status": "in_stock",
                   "source_url": null}]}

`sku` is the business's own product code and the stable key: importing the
same file twice updates products instead of duplicating them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import StockStatus
from app.db.models import Product


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=128)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    description: str = ""
    key_attributes: dict[str, Any] = Field(default_factory=dict)
    stock_status: StockStatus = StockStatus.IN_STOCK
    source_url: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("sku", "name", "category")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class CatalogError(ValueError):
    pass


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0


def load_catalog_file(path: str | Path) -> list[ProductIn]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CatalogError(f"Catalog file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise CatalogError(f"Catalog file is not valid JSON: {exc}") from None

    items = data.get("products") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise CatalogError('Catalog must be an object with a "products" list')

    products: list[ProductIn] = []
    errors: list[str] = []
    for i, item in enumerate(items):
        try:
            products.append(ProductIn.model_validate(item))
        except ValidationError as exc:
            label = item.get("sku", f"#{i}") if isinstance(item, dict) else f"#{i}"
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                errors.append(f"product {label}: {loc}: {err['msg']}")
    skus = [p.sku for p in products]
    dupes = sorted({s for s in skus if skus.count(s) > 1})
    if dupes:
        errors.append(f"duplicate sku(s): {', '.join(dupes)}")
    if errors:
        raise CatalogError("Catalog has errors:\n  " + "\n  ".join(errors))
    return products


def import_catalog(session: Session, user_id: str, products: list[ProductIn]) -> ImportResult:
    result = ImportResult()
    existing = {p.sku: p for p in session.scalars(select(Product).where(Product.user_id == user_id))}
    for item in products:
        values = item.model_dump()
        row = existing.get(item.sku)
        if row is None:
            session.add(Product(user_id=user_id, **values))
            result.created += 1
            continue
        changed = False
        for key, value in values.items():
            current = getattr(row, key)
            if key == "price":
                current = Decimal(str(current)).quantize(Decimal("0.01"))
                value = Decimal(str(value)).quantize(Decimal("0.01"))
            if current != value:
                setattr(row, key, value)
                changed = True
        if changed:
            result.updated += 1
        else:
            result.unchanged += 1
    session.flush()
    return result
