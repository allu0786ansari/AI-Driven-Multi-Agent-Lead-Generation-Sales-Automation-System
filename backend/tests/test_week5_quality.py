"""Week 5: fixes found by the evaluation harness, and the harness itself."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.comments import classify_rules
from app.db.enums import CommentIntent, NotificationKind
from app.db.models import Lead, Message, Notification
from app.db.session import session_scope
from app.dev import payloads as p
from app.qualify.slots import needs_support
from app.worker.processor import run_once
from tests.conftest import signed_post

PHONE = "919811000111"


@pytest.mark.parametrize(
    "text",
    [
        "the mixer stopped working", "my order 4521 has not arrived yet", "cancel my order please",
        "Do you repair old mixers?", "the kettle I bought leaks", "parcel abhi tak nahi aaya",
        "ennoda order innum varala", "I want a refund",
    ],
)  # fmt: skip
def test_support_requests(text: str) -> None:
    assert needs_support(text)


@pytest.mark.parametrize(
    "text",
    ["kettle price?", "is the 6L in stock", "return policy?", "air fryer with window", "order kaise kare?"],
)
def test_not_support_requests(text: str) -> None:
    assert not needs_support(text)


def test_complaint_in_a_dm_goes_to_a_person(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "the mixer I bought stopped working"))
    run_once()
    with session_scope() as s:
        lead = s.scalar(select(Lead).where(Lead.contact_phone == "+" + PHONE))
        assert lead.is_human_takeover
        [reply] = s.scalars(
            select(Message).where(Message.lead_id == lead.lead_id, Message.direction == "outbound")
        )
        assert reply.text.startswith("Sorry to hear that") and reply.sender == "system"
        [alert] = s.scalars(select(Notification).where(Notification.lead_id == lead.lead_id))
        assert alert.kind == NotificationKind.HANDOFF


def test_quantity_before_a_product_word(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(PHONE, "next month lena hai, 2 mixer"))
    run_once()
    with session_scope() as s:
        lead = s.scalar(select(Lead).where(Lead.contact_phone == "+" + PHONE))
        assert lead.qualify_slots["quantity"] == 2


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Beautiful colour", CommentIntent.OTHER),  # "colour" alone is not a question
        ("Visit my page for cheap phones", CommentIntent.SPAM),
        ("ennoda order innum varala", CommentIntent.COMPLAINT),
        ("Which colours?", CommentIntent.PRODUCT_QUESTION),
    ],
)
def test_comment_rules(text: str, intent: CommentIntent) -> None:
    assert classify_rules(text) == intent


def test_evaluation_harness_on_both_sample_sets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import evaluate

    monkeypatch.setattr(evaluate, "REPORT_DIR", tmp_path)
    assert evaluate.main([]) == 0
    assert evaluate.main(["--file", str(evaluate.DEFAULT_FILE.with_name("agents_holdout.sample.jsonl"))]) == 0
    [report] = sorted(tmp_path.glob("*.json"))[-1:]
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["metrics"]["no_wrong_price"]["correct"] == data["metrics"]["no_wrong_price"]["total"]


def test_evaluation_reports_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import evaluate

    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({"id": "a", "kind": "message", "text": "kettle price?", "expect": {"skus": ["AF-6L-WIN"]}})
        + "\n"
        + json.dumps({"id": "b", "kind": "comment", "text": "nice", "intent": "complaint"})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(evaluate, "REPORT_DIR", tmp_path / "reports")
    assert evaluate.main(["--file", str(cases)]) == 1  # targets missed
    md = next((tmp_path / "reports").glob("*.md")).read_text(encoding="utf-8")
    assert "`a`" in md and "`b`" in md and "**missed**" in md
