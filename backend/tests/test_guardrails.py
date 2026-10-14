from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents.guardrails import render_placeholders, validate_reply
from app.rag.records import ProductRecord

AF = ProductRecord(
    product_id="1", sku="AF-4L-DIG", name="CrispAir 4.2L Digital Air Fryer", category="Air Fryers",
    price=Decimal("4999.00"), currency="INR",
    description="4.2 litre air fryer with 8 presets. Cooks with up to 85% less oil.",
    key_attributes={"capacity_litres": 4.2, "presets": 8, "power_watts": 1400}, stock_status="in_stock",
)  # fmt: skip
WP = ProductRecord(
    product_id="2", sku="WP-RO-UV-8L", name="PureSip RO+UV 8L Water Purifier", category="Water Purifiers",
    price=Decimal("12999.00"), currency="INR", description="Free installation in serviced cities.",
    key_attributes={"storage_litres": 8}, stock_status="out_of_stock",
)  # fmt: skip
PRODUCTS = [AF, WP]


def test_render_placeholders() -> None:
    text, unknown = render_placeholders(
        "The {{name:AF-4L-DIG}} costs {{ price : af-4l-dig }} and is {{stock:AF-4L-DIG}}. "
        "Purifier: {{price:WP-RO-UV-8L}}, {{stock:WP-RO-UV-8L}}.",
        PRODUCTS,
    )
    assert text == (
        "The CrispAir 4.2L Digital Air Fryer costs ₹4,999 and is in stock. "
        "Purifier: ₹12,999, currently out of stock."
    )
    assert unknown == []


def test_render_unknown_sku_reported() -> None:
    text, unknown = render_placeholders("Only {{price:XYZ-1}}", PRODUCTS)
    assert unknown == ["XYZ-1"]
    assert "{{price:XYZ-1}}" in text


@pytest.mark.parametrize(
    "reply",
    [
        "The air fryer is ₹4,999 and cooks with 85% less oil. Want one?",
        "It has 8 presets and 1400 W power. Rs. 12,999 for the purifier, with free installation.",
        "Sure! 2 of them would be fine. Delivery to your city?",
        "Aap 3 jar wala chahte ho? Ye 4.2 litre ka hai.",
    ],
)
def test_valid_replies(reply: str) -> None:
    result = validate_reply(reply, PRODUCTS, customer_text="I need 2")
    assert result.ok, result.problems


@pytest.mark.parametrize(
    ("reply", "problem"),
    [
        ("The air fryer is only ₹3,999 today!", "not a catalog price"),
        ("Price is 4500/- only", "not a catalog price"),
        ("Get 50% off this week", "percentage"),
        ("It comes with a 24-month warranty and 3500 W power", "number '3500'"),
        ("We have a special discount for you", "unsupported claim 'discount'"),
        ("Free delivery anywhere in India", "unsupported claim 'free delivery'"),
        ("Pay with EMI from Rs 4999", "unsupported claim 'emi'"),
        ("It costs {{price:AF-4L-DIG}}", "unfilled placeholder"),
    ],
)
def test_invalid_replies(reply: str, problem: str) -> None:
    result = validate_reply(reply, PRODUCTS)
    assert not result.ok
    assert any(problem in p for p in result.problems), result.problems


def test_customer_numbers_allowed() -> None:
    assert validate_reply("Yes, we can deliver to pincode 600001.", PRODUCTS, "deliver to 600001?").ok
    assert not validate_reply("Yes, we can deliver to pincode 600001.", PRODUCTS, "deliver?").ok
