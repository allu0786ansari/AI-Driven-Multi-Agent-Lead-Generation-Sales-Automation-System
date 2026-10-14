from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.catalog.loader import CatalogError, import_catalog, load_catalog_file
from app.config import get_settings
from app.db.models import Product
from app.db.session import session_scope


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_sample_catalog_is_valid() -> None:
    products = load_catalog_file(get_settings().catalog_path)
    assert len(products) == 8
    assert {p.stock_status for p in products} >= {"in_stock", "out_of_stock", "preorder", "low_stock"}


def test_import_is_idempotent_and_updates(user, tmp_path: Path) -> None:  # noqa: ANN001
    products = load_catalog_file(get_settings().catalog_path)
    with session_scope() as s:
        r1 = import_catalog(s, user.user_id, products)
    assert (r1.created, r1.updated, r1.unchanged) == (8, 0, 0)

    with session_scope() as s:
        r2 = import_catalog(s, user.user_id, products)
    assert (r2.created, r2.updated, r2.unchanged) == (0, 0, 8)

    changed = [
        p.model_copy(update={"price": Decimal("999.50")}) if p.sku == "EK-1.8-SS" else p for p in products
    ]
    with session_scope() as s:
        r3 = import_catalog(s, user.user_id, changed)
    assert (r3.created, r3.updated, r3.unchanged) == (0, 1, 7)

    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Product)) == 8
        kettle = s.scalar(select(Product).where(Product.sku == "EK-1.8-SS"))
        assert kettle.price == Decimal("999.50")
        assert kettle.user_id == user.user_id


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"items": []}, '"products" list'),
        ({"products": [{"sku": "A", "name": "x", "category": "c", "price": 0}]}, "price"),
        ({"products": [{"sku": "A", "name": " ", "category": "c", "price": 10}]}, "name"),
        (
            {"products": [{"sku": "A", "name": "x", "category": "c", "price": 10, "stock_status": "maybe"}]},
            "stock_status",
        ),
        (
            {
                "products": [
                    {"sku": "A", "name": "x", "category": "c", "price": 1},
                    {"sku": "A", "name": "y", "category": "c", "price": 2},
                ]
            },
            "duplicate sku",
        ),
    ],
)
def test_invalid_catalogs_rejected(tmp_path: Path, data: object, message: str) -> None:
    with pytest.raises(CatalogError, match=message):
        load_catalog_file(_write(tmp_path, data))


def test_missing_and_broken_files(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="not found"):
        load_catalog_file(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    with pytest.raises(CatalogError, match="not valid JSON"):
        load_catalog_file(bad)


def test_currency_uppercased(tmp_path: Path) -> None:
    [p] = load_catalog_file(
        _write(
            tmp_path,
            {"products": [{"sku": "A", "name": "x", "category": "c", "price": 5, "currency": "inr"}]},
        )
    )
    assert p.currency == "INR"
