"""Qualify Agent: rule extraction, scoring rubric, next question, config API, status transitions."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.db.models import Lead
from app.db.session import session_scope
from app.qualify.agent import process_message
from app.qualify.config import QualificationConfig, get_config, save_config
from app.qualify.scoring import next_question, score, summarize
from app.qualify.slots import QualifySlots, extract_rules


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("6L air fryer chahiye, budget 7000, is hafte Chennai delivery",
         {"budget": Decimal("7000"), "timeline": "this_week", "city": "Chennai", "purchase_intent": "medium"}),
        ("I want to order 3 pcs today, pincode 600001",
         {"quantity": 3, "timeline": "now", "city": "PIN 600001", "purchase_intent": "high"}),
        ("not now, just checking", {"timeline": "later"}),
        ("worst product, I want a refund. call me", {"sentiment": "negative", "wants_human": True}),
        ("evlo price? 5k tak", {"budget": Decimal("5000"), "purchase_intent": "medium"}),
        ("qty: 12 for my shop, next week", {"quantity": 12, "timeline": "this_month"}),
        ("hello", {}),
    ],
)  # fmt: skip
def test_extract_rules(text: str, expected: dict) -> None:
    got = extract_rules(text).model_dump(exclude_none=True)
    got.pop("wants_human") if got.get("wants_human") is False else None
    assert got == expected


def test_merge_newer_wins_and_wants_human_sticks() -> None:
    old = QualifySlots(city="Chennai", budget=Decimal(5000), wants_human=True)
    new = QualifySlots(budget=Decimal(7000), quantity=2)
    merged = old.merged_with(new)
    assert (merged.city, merged.budget, merged.quantity, merged.wants_human) == (
        "Chennai",
        Decimal(7000),
        2,
        True,
    )


def test_score_rubric_and_reasons() -> None:
    cfg = QualificationConfig()
    full = QualifySlots(
        product="CrispAir 6L",
        budget=Decimal(15000),
        timeline="now",
        quantity=3,
        city="Chennai",
        purchase_intent="high",
    )
    r = score(full, cfg, inbound_messages=2, reference_price=Decimal(6799))
    assert r.score == 100 and r.status == "hot"  # 20+10+10+25+10+5+10+15+5 = 110, capped
    assert "budget covers the product (+10)" in r.rationale and "bulk order (+5)" in r.rationale

    warm = score(QualifySlots(product="Kettle", timeline="this_month", purchase_intent="medium"), cfg)
    assert (warm.score, warm.status) == (43, "warm")
    cold = score(QualifySlots(timeline="later", sentiment="negative"), cfg)
    assert (cold.score, cold.status) == (0, "cold")
    assert score(QualifySlots(), cfg).rationale == "not enough information yet"


def test_budget_below_price_gets_no_fit_points() -> None:
    r = score(QualifySlots(budget=Decimal(3000)), QualificationConfig(), reference_price=Decimal(4999))
    assert r.score == 10


def test_next_question_follows_config_order() -> None:
    cfg = QualificationConfig()
    assert next_question(QualifySlots(), cfg) == "Which product are you interested in?"
    assert next_question(QualifySlots(category="Air Fryers"), cfg) == "How many would you need?"
    done = QualifySlots(product="x", quantity=1, timeline="now", city="Pune", budget=Decimal(1))
    assert next_question(done, cfg) is None


def test_summary() -> None:
    s = QualifySlots(
        product="CrispAir 6L", quantity=2, budget=Decimal(15000), timeline="this_week", city="Chennai"
    )
    assert summarize(s) == "Wants: CrispAir 6L · Qty 2 · Budget ₹15,000 · Timeline: this week · City: Chennai"
    assert summarize(QualifySlots()) == "No details yet"


def test_config_validation() -> None:
    with pytest.raises(ValidationError, match="warm_threshold must be lower"):
        QualificationConfig(hot_threshold=50, warm_threshold=60)
    with pytest.raises(ValidationError, match="only one question"):
        QualificationConfig(
            questions=[{"slot": "city", "question": "City?"}, {"slot": "city", "question": "Where?"}]
        )


def test_config_saved_per_business(user) -> None:  # noqa: ANN001
    with session_scope() as s:
        assert get_config(s, user.user_id).hot_threshold == 70
        save_config(s, user.user_id, QualificationConfig(hot_threshold=60, warm_threshold=30))
    with session_scope() as s:
        assert get_config(s, user.user_id).hot_threshold == 60


def test_settings_api(client: TestClient, auth_headers: dict) -> None:
    body = client.get("/api/settings/qualification", headers=auth_headers).json()
    assert body["questions"][0]["slot"] == "product"
    body["questions"] = [{"slot": "city", "question": "Which city?"}]
    body["hot_threshold"] = 50
    assert client.put("/api/settings/qualification", json=body, headers=auth_headers).status_code == 200
    again = client.get("/api/settings/qualification", headers=auth_headers).json()
    assert again["hot_threshold"] == 50 and len(again["questions"]) == 1
    bad = {**again, "warm_threshold": 90}
    assert client.put("/api/settings/qualification", json=bad, headers=auth_headers).status_code == 422
    assert client.get("/api/settings/qualification").status_code == 401


def _lead(user_id: str, **kw) -> str:  # noqa: ANN003
    with session_scope() as s:
        lead = Lead(user_id=user_id, qualify_slots={}, contact_phone="+919800011111", **kw)
        s.add(lead)
        s.flush()
        return lead.lead_id


def test_process_message_updates_lead_and_flags_hot_once(catalog) -> None:  # noqa: ANN001
    lead_id = _lead(catalog.user_id)
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        q1 = process_message(s, lead, "air fryer price?")
        assert (q1.status, q1.became_hot) == ("cold", False)
        llm = QualifySlots(product="CrispAir 6L Air Fryer with Window", quantity=2)
        q2 = process_message(s, lead, "need it today, budget 15000, Chennai, order karna hai", llm_slots=llm)
        assert (q2.status, q2.became_hot) == ("hot", True)
        assert lead.qualification_score >= 70
        assert lead.product_interest == "CrispAir 6L Air Fryer with Window"
        assert lead.conversation_summary.startswith("Wants: CrispAir 6L")
        assert lead.qualify_slots["city"] == "Chennai"
        q3 = process_message(s, lead, "ok")
        assert q3.became_hot is False  # already hot


def test_salesperson_status_not_overwritten(catalog) -> None:  # noqa: ANN001
    lead_id = _lead(catalog.user_id, status="converted")
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        q = process_message(s, lead, "order 3 pcs today Chennai budget 20000")
        assert lead.status == "converted" and q.became_hot is False
        assert lead.qualification_score > 0  # score still tracked
