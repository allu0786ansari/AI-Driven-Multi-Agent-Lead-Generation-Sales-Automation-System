"""Capture Agent: phone normalization, WhatsApp and lead-form capture, consent."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.agents.capture import (
    capture_lead_form,
    capture_whatsapp_message,
    extract_whatsapp_text,
    normalize_phone,
)
from app.db.models import ConsentEvent, Lead, Message
from app.db.session import session_scope
from app.dev import payloads as p
from app.integrations.meta_graph import FakeMetaGraphClient


def _wa_payload(raw: dict) -> dict:
    """The parsed-event payload the handler receives (see app/webhooks/parse.py)."""
    value = raw["entry"][0]["changes"][0]["value"]
    return {"metadata": value["metadata"], "contact": value["contacts"][0], "message": value["messages"][0]}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+91 98765-43210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("9876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("0044 20 7946 0958", "+442079460958"),
        ("12345", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_phone(raw, expected) -> None:  # noqa: ANN001
    assert normalize_phone(raw, "91") == expected


def test_whatsapp_new_lead_and_windows(user) -> None:  # noqa: ANN001
    raw = p.whatsapp_text("919876543210", "hello", name="Priya", msg_id="wamid.X1")
    with session_scope() as s:
        r = capture_whatsapp_message(s, user.user_id, _wa_payload(raw))
        assert r.created
        lead = r.lead
        assert (lead.contact_phone, lead.name, lead.source) == ("+919876543210", "Priya", "dm")
        assert lead.service_window_expires_at - lead.last_inbound_at == timedelta(hours=24)
        assert lead.ctwa_message_at is None
        assert (r.text, r.media_type) == ("hello", "none")
        assert r.message.platform_message_id == "wamid.X1"
        assert r.message.direction == "inbound"


def test_whatsapp_ctwa_attribution_and_reuse(user) -> None:  # noqa: ANN001
    raw = p.whatsapp_ctwa("919876543210", ad_id="AD77", ctwa_clid="CLID77", headline="Air fryer sale")
    with session_scope() as s:
        first = capture_whatsapp_message(s, user.user_id, _wa_payload(raw))
        lead_id = first.lead.lead_id
        assert (first.lead.source, first.lead.ad_id, first.lead.ctwa_clid) == ("ctwa_ad", "AD77", "CLID77")
        assert first.lead.ctwa_message_at is not None
        assert first.lead.source_detail == "CTWA ad: Air fryer sale"
    with session_scope() as s:
        second = capture_whatsapp_message(
            s, user.user_id, _wa_payload(p.whatsapp_text("919876543210", "again"))
        )
        assert not second.created
        assert second.lead.lead_id == lead_id
        assert second.lead.source == "ctwa_ad"  # first touch is kept
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Message)) == 2


def test_extract_text_variants() -> None:
    assert extract_whatsapp_text({"type": "button", "button": {"text": "See prices"}}) == (
        "See prices",
        "none",
    )
    assert extract_whatsapp_text(
        {"type": "interactive", "interactive": {"button_reply": {"title": "Yes"}}}
    ) == ("Yes", "none")
    assert extract_whatsapp_text({"type": "image", "image": {"caption": "this one?"}}) == (
        "this one?",
        "image",
    )
    assert extract_whatsapp_text({"type": "audio", "audio": {"id": "1"}}) == (None, "audio")


def test_lead_form_with_opt_in_records_consent(user) -> None:  # noqa: ANN001
    data = FakeMetaGraphClient().fetch_lead("12345678")
    with session_scope() as s:
        r = capture_lead_form(s, user.user_id, data)
        lead = r.lead
        assert lead.name == "Form Lead 5678"
        assert lead.contact_phone == "+919812345678"
        assert (lead.source, lead.meta_lead_id, lead.ad_id, lead.campaign_id) == (
            "ad_form", "12345678", "120200000000000002", "120200000000000009",
        )  # fmt: skip
        assert lead.product_interest == "Air Fryers"
        assert lead.whatsapp_opt_in and lead.opt_in_source == "lead_form"
    with session_scope() as s:
        consent = s.scalar(select(ConsentEvent))
        assert consent.type == "opt_in" and "WhatsApp" in consent.wording_shown


def test_lead_form_without_opt_in(user) -> None:  # noqa: ANN001
    data = FakeMetaGraphClient().fetch_lead("000111")
    with session_scope() as s:
        lead = capture_lead_form(s, user.user_id, data).lead
        assert not lead.whatsapp_opt_in
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(ConsentEvent)) == 0


def test_lead_form_disclaimer_checkbox(user) -> None:  # noqa: ANN001
    data = {
        "id": "777",
        "field_data": [
            {"name": "full_name", "values": ["Ravi K"]},
            {"name": "phone_number", "values": ["9876500000"]},
        ],
        "custom_disclaimer_responses": [{"checkbox_key": "whatsapp_opt_in", "is_checked": "1"}],
    }
    with session_scope() as s:
        assert capture_lead_form(s, user.user_id, data).lead.whatsapp_opt_in


def test_lead_form_matches_existing_whatsapp_lead(user) -> None:  # noqa: ANN001
    with session_scope() as s:
        wa = capture_whatsapp_message(s, user.user_id, _wa_payload(p.whatsapp_text("919812345678", "hi")))
        wa_id = wa.lead.lead_id
    with session_scope() as s:
        lead = capture_lead_form(s, user.user_id, FakeMetaGraphClient().fetch_lead("12345678")).lead
        assert lead.lead_id == wa_id
        assert lead.source == "dm"  # first touch kept
        assert lead.meta_lead_id == "12345678"
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Lead)) == 1
