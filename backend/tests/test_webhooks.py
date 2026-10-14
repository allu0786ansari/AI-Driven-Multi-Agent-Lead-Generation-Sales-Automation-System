"""Webhook layer: handshake, signatures, parsing, storage, de-duplication."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.enums import EventStatus, EventType
from app.db.models import InboundEvent
from app.db.session import session_scope
from app.dev import payloads as p
from app.webhooks.parse import parse_webhook
from app.webhooks.signature import compute_signature, is_valid_signature
from tests.conftest import signed_post

ENDPOINTS = ["/webhooks/meta/page", "/webhooks/meta/instagram", "/webhooks/whatsapp"]


def _events() -> list[InboundEvent]:
    with session_scope() as s:
        return list(s.scalars(select(InboundEvent).order_by(InboundEvent.received_at)))


# ------------------------------------------------------------------ handshake


@pytest.mark.parametrize("path", ENDPOINTS)
def test_verify_handshake_ok(client: TestClient, path: str) -> None:
    resp = client.get(
        path,
        params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "12345"},
    )
    assert resp.status_code == 200
    assert resp.text == "12345"


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": "test-verify-token"},
        {},
    ],
)
def test_verify_handshake_rejected(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/webhooks/whatsapp", params=params).status_code == 403


# ----------------------------------------------------------------- signatures


def test_signature_helpers() -> None:
    body = b'{"a":1}'
    sig = compute_signature(body, "s3cret")
    assert sig.startswith("sha256=")
    assert is_valid_signature(body, sig, "s3cret")
    assert not is_valid_signature(body, sig, "other")
    assert not is_valid_signature(body + b" ", sig, "s3cret")
    assert not is_valid_signature(body, None, "s3cret")
    assert not is_valid_signature(body, "sha1=abc", "s3cret")


def test_unsigned_request_rejected_and_not_stored(client: TestClient) -> None:
    resp = client.post("/webhooks/whatsapp", json=p.whatsapp_text())
    assert resp.status_code == 401
    assert _events() == []


def test_wrong_secret_rejected(client: TestClient) -> None:
    resp = signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(), secret="attacker")
    assert resp.status_code == 401
    assert _events() == []


def test_tampered_body_rejected(client: TestClient) -> None:
    body = json.dumps(p.whatsapp_text(text="original")).encode()
    sig = compute_signature(body, "test-app-secret")
    tampered = body.replace(b"original", b"tampered")
    resp = client.post("/webhooks/whatsapp", content=tampered, headers={"X-Hub-Signature-256": sig})
    assert resp.status_code == 401


def test_invalid_json_rejected(client: TestClient) -> None:
    assert signed_post(client, "/webhooks/whatsapp", b"not json").status_code == 400
    assert signed_post(client, "/webhooks/whatsapp", b"[1,2]").status_code == 400


# ----------------------------------------------------------- storage + dedupe


def test_whatsapp_message_stored_pending(client: TestClient, user) -> None:  # noqa: ANN001
    resp = signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(msg_id="wamid.ONE", text="hello"))
    assert resp.status_code == 200
    assert resp.json() == {"stored": 1, "duplicates": 0, "skipped": 0}
    [ev] = _events()
    assert ev.platform_event_id == "whatsapp.message:wamid.ONE"
    assert ev.event_type == EventType.WHATSAPP_MESSAGE
    assert ev.status == EventStatus.PENDING
    assert ev.user_id == user.user_id
    assert ev.endpoint == "whatsapp"
    assert ev.payload["message"]["text"]["body"] == "hello"
    assert ev.payload["contact"]["profile"]["name"] == "Test Customer"
    assert ev.payload["metadata"]["phone_number_id"] == p.DEFAULT_PHONE_NUMBER_ID
    assert len(ev.trace_id) == 16


def test_duplicate_delivery_stored_once(client: TestClient) -> None:
    payload = p.whatsapp_text(msg_id="wamid.DUP")
    assert signed_post(client, "/webhooks/whatsapp", payload).json()["stored"] == 1
    second = signed_post(client, "/webhooks/whatsapp", payload)
    assert second.status_code == 200  # Meta must still get 200, or it keeps retrying
    assert second.json() == {"stored": 0, "duplicates": 1, "skipped": 0}
    assert len(_events()) == 1


def test_duplicates_within_one_body(client: TestClient) -> None:
    payload = p.whatsapp_text(msg_id="wamid.SAME")
    payload["entry"].append(payload["entry"][0])
    assert signed_post(client, "/webhooks/whatsapp", payload).json() == {
        "stored": 1,
        "duplicates": 1,
        "skipped": 0,
    }


def test_event_without_user_has_null_user_id(client: TestClient) -> None:
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text())
    [ev] = _events()
    assert ev.user_id is None


def test_status_steps_are_separate_events(client: TestClient) -> None:
    for state in ("sent", "delivered", "read", "read"):
        signed_post(client, "/webhooks/whatsapp", p.whatsapp_status("wamid.OUT1", state))
    ids = [e.platform_event_id for e in _events()]
    assert ids == [
        "whatsapp.status:wamid.OUT1:sent",
        "whatsapp.status:wamid.OUT1:delivered",
        "whatsapp.status:wamid.OUT1:read",
    ]


@pytest.mark.parametrize(
    ("payload", "path", "expected_type", "id_prefix"),
    [
        (p.whatsapp_ctwa(), "/webhooks/whatsapp", EventType.WHATSAPP_MESSAGE, "whatsapp.message:"),
        (p.whatsapp_audio(), "/webhooks/whatsapp", EventType.WHATSAPP_MESSAGE, "whatsapp.message:"),
        (p.leadgen(leadgen_id="999"), "/webhooks/meta/page", EventType.PAGE_LEADGEN, "page.leadgen:999"),
        (p.facebook_comment(), "/webhooks/meta/page", EventType.PAGE_COMMENT, "page.comment:"),
        (p.messenger_message(), "/webhooks/meta/page", EventType.PAGE_MESSAGE, "page.message:"),
        (
            p.messenger_message(is_echo=True),
            "/webhooks/meta/page",
            EventType.PAGE_MESSAGE_ECHO,
            "page.message:",
        ),
        (
            p.instagram_comment(comment_id="c1"),
            "/webhooks/meta/instagram",
            EventType.INSTAGRAM_COMMENT,
            "instagram.comment:c1",
        ),
        (
            p.instagram_comment(live=True),
            "/webhooks/meta/instagram",
            EventType.INSTAGRAM_COMMENT,
            "instagram.comment:",
        ),
        (
            p.instagram_message(),
            "/webhooks/meta/instagram",
            EventType.INSTAGRAM_MESSAGE,
            "instagram.message:",
        ),
        (
            p.instagram_message(is_echo=True),
            "/webhooks/meta/instagram",
            EventType.INSTAGRAM_MESSAGE_ECHO,
            "instagram.message:",
        ),
    ],
)
def test_each_payload_type_classified(
    client: TestClient, payload: dict, path: str, expected_type: EventType, id_prefix: str
) -> None:
    assert signed_post(client, path, payload).json()["stored"] == 1
    [ev] = _events()
    assert ev.event_type == expected_type
    assert ev.platform_event_id.startswith(id_prefix)


def test_ctwa_referral_kept_in_payload(client: TestClient) -> None:
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_ctwa(ad_id="AD42", ctwa_clid="CLID42"))
    [ev] = _events()
    referral = ev.payload["message"]["referral"]
    assert referral["source_id"] == "AD42"
    assert referral["ctwa_clid"] == "CLID42"


@pytest.mark.parametrize(
    ("payload", "path"),
    [
        (p.facebook_reaction(), "/webhooks/meta/page"),
        (p.facebook_comment(verb="edited"), "/webhooks/meta/page"),
        (p.instagram_read_receipt(), "/webhooks/meta/instagram"),
    ],
)
def test_irrelevant_items_skipped(client: TestClient, payload: dict, path: str) -> None:
    assert signed_post(client, path, payload).json() == {"stored": 0, "duplicates": 0, "skipped": 1}
    assert _events() == []


def test_unknown_payload_kept_visible(client: TestClient) -> None:
    payload = {"object": "page", "entry": [{"id": "1", "changes": [{"field": "mention", "value": {"x": 1}}]}]}
    assert signed_post(client, "/webhooks/meta/page", payload).json()["stored"] == 1
    [ev] = _events()
    assert ev.event_type == EventType.UNKNOWN
    # Same unknown body again is a duplicate, not a second row.
    assert signed_post(client, "/webhooks/meta/page", payload).json()["duplicates"] == 1


def test_body_without_entries_is_unknown() -> None:
    result = parse_webhook({"object": "whatsapp_business_account"})
    assert [e.event_type for e in result.events] == [EventType.UNKNOWN]


def test_multiple_messages_in_one_body() -> None:
    payload = p.whatsapp_text(msg_id="wamid.A")
    extra = p.whatsapp_text(msg_id="wamid.B", from_phone="919999999999", name="Second")
    value = payload["entry"][0]["changes"][0]["value"]
    extra_value = extra["entry"][0]["changes"][0]["value"]
    value["messages"] += extra_value["messages"]
    value["contacts"] += extra_value["contacts"]
    result = parse_webhook(payload)
    assert [e.platform_event_id for e in result.events] == [
        "whatsapp.message:wamid.A",
        "whatsapp.message:wamid.B",
    ]
    # Each message is paired with its own sender's contact profile.
    assert result.events[1].payload["contact"]["profile"]["name"] == "Second"


def test_signature_check_can_be_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "meta_verify_signatures", False)
    assert client.post("/webhooks/whatsapp", json=p.whatsapp_text()).status_code == 200
