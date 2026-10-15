"""Week 4 backend for the dashboard: inbox, lead actions, manual messages, alerts, products,
templates, WebSocket and serving the built React app."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.api import ws as ws_module
from app.auth.security import create_access_token
from app.db.base import utcnow
from app.db.models import Lead, Message, ProductEmbedding
from app.db.session import session_scope
from app.dev import payloads as p
from app.integrations.factory import get_meta_messaging, get_whatsapp_sender
from app.worker.processor import run_once
from tests.conftest import signed_post

WA = "/webhooks/whatsapp"
IG = "/webhooks/meta/instagram"


def _lead_id(phone: str | None = None, igsid: str | None = None) -> str:
    with session_scope() as s:
        q = select(Lead.lead_id)
        q = (
            q.where(Lead.contact_phone == "+" + phone)
            if phone
            else q.where(Lead.instagram_scoped_id == igsid)
        )
        return s.scalar(q)


@pytest.fixture
def inbox(client: TestClient, catalog) -> dict[str, str]:  # noqa: ANN001
    """Four leads: hot (WhatsApp), needs a person, plain cold one, and an Instagram DM."""
    for text in (
        "Hi, I want the 6 litre air fryer",
        "Need 2 pcs, budget 15000, Chennai this week. Want to order",
    ):
        signed_post(client, WA, p.whatsapp_text("919811100000", text, name="Hot Lead"))
    signed_post(client, WA, p.whatsapp_text("919811100001", "Can someone call me please?", name="Caller"))
    signed_post(client, WA, p.whatsapp_text("919811100002", "kettle price?", name="Cold One"))
    signed_post(client, IG, p.instagram_message("induction cooktop price?", igsid="IGX"))
    run_once()
    return {
        "hot": _lead_id("919811100000"),
        "caller": _lead_id("919811100001"),
        "cold": _lead_id("919811100002"),
        "ig": _lead_id(igsid="IGX"),
    }


# ---------------------------------------------------------------------- inbox


def test_inbox_views_counts_and_previews(client: TestClient, inbox: dict, auth_headers: dict) -> None:
    counts = client.get("/api/leads/counts", headers=auth_headers).json()
    assert (counts["all"], counts["needs_you"], counts["hot"], counts["takeover"], counts["closed"]) == (
        4,
        2,
        1,
        1,
        0,
    )

    needs = client.get("/api/leads", params={"view": "needs_you"}, headers=auth_headers).json()
    assert {lead["lead_id"] for lead in needs} == {inbox["hot"], inbox["caller"]}
    caller = next(lead for lead in needs if lead["lead_id"] == inbox["caller"])
    assert caller["last_message_direction"] == "outbound" and caller["last_message_sender"] == "system"
    assert caller["unread_alerts"] == 1

    found = client.get("/api/leads", params={"q": "cold"}, headers=auth_headers).json()
    assert [lead["lead_id"] for lead in found] == [inbox["cold"]]
    ig_only = client.get("/api/leads", params={"channel": "instagram"}, headers=auth_headers).json()
    assert [lead["lead_id"] for lead in ig_only] == [inbox["ig"]]
    page2 = client.get("/api/leads", params={"limit": 2, "offset": 2}, headers=auth_headers).json()
    assert len(page2) == 2


def test_lead_detail_has_reply_options_and_kinds(client: TestClient, inbox: dict, auth_headers: dict) -> None:
    d = client.get(f"/api/leads/{inbox['ig']}", headers=auth_headers).json()
    assert d["default_channel"] == "instagram"
    [opt] = d["reply_options"]
    assert opt["channel"] == "instagram" and opt["free_text"] and not opt["templates"]
    assert [m["kind"] for m in d["messages"]] == ["message", "text"]

    wa = client.get(f"/api/leads/{inbox['cold']}", headers=auth_headers).json()
    [opt] = wa["reply_options"]
    assert opt["channel"] == "whatsapp" and opt["free_text"] and opt["templates"]


# ---------------------------------------------------------------- lead actions


def test_status_assign_and_activity(client: TestClient, inbox: dict, auth_headers: dict) -> None:
    lid = inbox["hot"]
    r = client.patch(
        f"/api/leads/{lid}", json={"status": "converted", "assigned_to": "Ravi"}, headers=auth_headers
    )
    assert r.status_code == 200 and r.json()["status"] == "converted" and r.json()["assigned_to"] == "Ravi"
    assert (
        client.patch(f"/api/leads/{lid}", json={"status": "nonsense"}, headers=auth_headers).status_code
        == 422
    )
    d = client.get(f"/api/leads/{lid}", headers=auth_headers).json()
    kinds = [(a["kind"], a["detail"].get("new")) for a in d["activity"]]
    assert kinds == [("status", "converted"), ("assign", "Ravi")]
    assert d["activity"][0]["actor"] == "owner@example.com" and d["activity"][0]["detail"]["old"] == "hot"
    counts = client.get("/api/leads/counts", headers=auth_headers).json()
    assert counts["closed"] == 1 and counts["needs_you"] == 1  # converted leads no longer need you


def test_takeover_silences_assistant_and_release_resumes(
    client: TestClient, inbox: dict, auth_headers: dict
) -> None:
    lid = inbox["cold"]
    assert client.post(f"/api/leads/{lid}/takeover", headers=auth_headers).json()["is_human_takeover"] is True
    sent_before = len(get_whatsapp_sender().sent)
    signed_post(client, WA, p.whatsapp_text("919811100002", "hello?"))
    run_once()
    assert len(get_whatsapp_sender().sent) == sent_before  # assistant silent

    assert client.post(f"/api/leads/{lid}/release", headers=auth_headers).json()["is_human_takeover"] is False
    signed_post(client, WA, p.whatsapp_text("919811100002", "kettle price again?"))
    run_once()
    assert len(get_whatsapp_sender().sent) == sent_before + 1  # assistant answers again
    acts = [a["kind"] for a in client.get(f"/api/leads/{lid}", headers=auth_headers).json()["activity"]]
    assert acts == ["takeover", "release"]


def test_other_business_lead_is_404(client: TestClient, inbox: dict) -> None:
    from app.auth.service import create_user

    with session_scope() as s:
        other = create_user(s, "other@example.com", "another-password")
    headers = {"Authorization": f"Bearer {create_access_token(other.user_id)}"}
    for method, path in (("get", ""), ("post", "/takeover"), ("patch", "")):
        kwargs = {"json": {"status": "lost"}} if method == "patch" else {}
        assert (
            getattr(client, method)(f"/api/leads/{inbox['hot']}{path}", headers=headers, **kwargs).status_code
            == 404
        )
    assert client.get("/api/leads", headers=headers).json() == []


# ------------------------------------------------------------- manual messages


def test_manual_whatsapp_message_takes_over_and_is_sent(
    client: TestClient, inbox: dict, auth_headers: dict
) -> None:
    lid = inbox["cold"]
    body = {"text": "Hi, this is Ravi from the store. Shall I call you?", "client_message_id": "abc123"}
    r = client.post(f"/api/leads/{lid}/message", json=body, headers=auth_headers).json()
    assert r["decision"] == "queued" and r["message"]["sender"] == "salesperson"
    again = client.post(f"/api/leads/{lid}/message", json=body, headers=auth_headers).json()
    assert again["reason"] == "already queued (idempotent)"  # double click: one message
    run_once()
    assert get_whatsapp_sender().sent[-1]["body"] == body["text"]
    d = client.get(f"/api/leads/{lid}", headers=auth_headers).json()
    assert d["is_human_takeover"] and d["assigned_to"] == "owner@example.com"
    assert [a["kind"] for a in d["activity"]] == ["takeover", "message"]


def test_manual_message_outside_window_needs_template(
    client: TestClient, inbox: dict, auth_headers: dict
) -> None:
    lid = inbox["cold"]
    with session_scope() as s:
        s.get(Lead, lid).service_window_expires_at = utcnow() - timedelta(minutes=1)
    opts = client.get(f"/api/leads/{lid}", headers=auth_headers).json()["reply_options"]
    assert opts[0]["free_text"] is False and "template" in opts[0]["reason"]
    r = client.post(f"/api/leads/{lid}/message", json={"text": "hello"}, headers=auth_headers).json()
    assert r["decision"] == "needs_template"
    # Marketing template needs opt-in; utility does not.
    tpl = {"template": {"name": "order_update", "params": ["Cold One"], "category": "marketing"}}
    assert (
        client.post(f"/api/leads/{lid}/message", json=tpl, headers=auth_headers).json()["decision"]
        == "blocked"
    )
    tpl["template"]["category"] = "utility"
    assert (
        client.post(f"/api/leads/{lid}/message", json=tpl, headers=auth_headers).json()["decision"]
        == "queued"
    )


def test_manual_instagram_reply(client: TestClient, inbox: dict, auth_headers: dict) -> None:
    r = client.post(
        f"/api/leads/{inbox['ig']}/message", json={"text": "Yes, in stock!"}, headers=auth_headers
    ).json()
    assert r["decision"] == "queued" and r["message"]["channel"] == "instagram"
    run_once()
    assert get_meta_messaging().sent[-1] == {
        **get_meta_messaging().sent[-1],
        "type": "dm",
        "recipient_id": "IGX",
    }


@pytest.mark.parametrize("body", [{}, {"text": "  "}, {"text": "a", "template": {"name": "x"}}])
def test_manual_message_validation(client: TestClient, inbox: dict, auth_headers: dict, body: dict) -> None:
    assert (
        client.post(f"/api/leads/{inbox['cold']}/message", json=body, headers=auth_headers).status_code == 422
    )


# -------------------------------------------------------------------- alerts


def test_notifications_unread_and_mark_read(client: TestClient, inbox: dict, auth_headers: dict) -> None:
    assert client.get("/api/notifications/unread-count", headers=auth_headers).json() == {"unread": 2}
    unread = client.get("/api/notifications", params={"unread": True}, headers=auth_headers).json()
    assert {n["kind"] for n in unread} == {"hot_lead", "handoff"}
    r = client.post("/api/notifications/read", json={"lead_id": inbox["caller"]}, headers=auth_headers)
    assert r.json() == {"marked": 1}
    assert client.get("/api/notifications/unread-count", headers=auth_headers).json() == {"unread": 1}
    assert client.post("/api/notifications/read", json={"all": True}, headers=auth_headers).json() == {
        "marked": 1
    }
    assert client.post("/api/notifications/read", json={}, headers=auth_headers).json() == {"marked": 0}


# ------------------------------------------------------------------ products


def test_product_crud(client: TestClient, catalog, auth_headers: dict) -> None:  # noqa: ANN001
    products = client.get("/api/products", headers=auth_headers).json()
    assert len(products) == 8
    assert client.get("/api/products/categories", headers=auth_headers).json()[0] == "Air Fryers"
    new = {
        "sku": "TO-2S",
        "name": "CrispToast 2-Slice Toaster",
        "category": "Toasters",
        "price": 1499,
        "description": "Pop-up toaster",
    }
    created = client.post("/api/products", json=new, headers=auth_headers)
    assert created.status_code == 201
    pid = created.json()["product_id"]
    assert client.post("/api/products", json=new, headers=auth_headers).status_code == 409
    upd = client.patch(
        f"/api/products/{pid}", json={"price": "1299.00", "stock_status": "low_stock"}, headers=auth_headers
    )
    assert upd.json()["price"] == "1299.00" and upd.json()["stock_status"] == "low_stock"
    assert client.patch(f"/api/products/{pid}", json={"price": 0}, headers=auth_headers).status_code == 422
    assert client.patch(f"/api/products/{pid}", json={"name": " "}, headers=auth_headers).status_code == 422
    # The new product is searchable right away (re-embedded on demand).
    hits = client.get("/api/products/search", params={"q": "toaster"}, headers=auth_headers).json()["results"]
    assert hits[0]["sku"] == "TO-2S" and hits[0]["price"] == "1299.00"
    assert (
        client.get("/api/products", params={"q": "toast"}, headers=auth_headers).json()[0]["sku"] == "TO-2S"
    )
    assert client.delete(f"/api/products/{pid}", headers=auth_headers).status_code == 204
    with session_scope() as s:
        assert s.get(ProductEmbedding, pid) is None  # vector removed with the product
    assert client.delete(f"/api/products/{pid}", headers=auth_headers).status_code == 404


# ----------------------------------------------------------------- templates


def test_templates_from_settings(client: TestClient, auth_headers: dict) -> None:
    body = client.get("/api/templates", headers=auth_headers).json()
    assert body["source"] == "settings"
    welcome = body["templates"][0]
    assert (
        welcome["name"] == "lead_form_welcome"
        and welcome["param_count"] == 2
        and welcome["status"] == "not checked"
    )


def test_templates_from_meta(client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import templates as tmod
    from app.config import get_settings

    s = get_settings()
    for k, v in {
        "meta_api_mode": "live",
        "whatsapp_business_account_id": "WABA1",
        "whatsapp_access_token": "T",
    }.items():
        monkeypatch.setattr(s, k, v)
    seen = {}

    def fake_request(http, method, url, what, **kw):  # noqa: ANN001, ANN003, ANN202
        seen["url"] = url
        return {"data": [{"name": "order_update", "status": "APPROVED", "category": "UTILITY", "language": "en",
                          "components": [{"type": "BODY", "text": "Hi {{1}}, your order {{2}} ships today."}]}]}  # fmt: skip

    monkeypatch.setattr(tmod, "request", fake_request)
    body = client.get("/api/templates", headers=auth_headers).json()
    assert body["source"] == "meta" and seen["url"].endswith("/WABA1/message_templates")
    assert body["templates"][0] == {
        **body["templates"][0],
        "category": "utility",
        "status": "APPROVED",
        "param_count": 2,
    }


# ----------------------------------------------------------------- websocket


def test_websocket_pushes_alerts_and_lead_changes(
    client: TestClient, catalog, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr(ws_module, "POLL_SECONDS", 0.05)
    token = auth_headers["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/notifications?token={token}") as ws:
        assert ws.receive_json() == {"type": "hello", "unread": 0}
        ws.send_text("ping")
        assert ws.receive_text() == "pong"
        signed_post(client, WA, p.whatsapp_text("919811100001", "Can someone call me please?"))
        run_once()
        got = {}
        while len(got) < 2:
            msg = ws.receive_json()
            got[msg["type"]] = msg
        assert got["notification"]["notification"]["kind"] == "handoff"
        assert got["leads_changed"]["lead_ids"] == [_lead_id("919811100001")]


def test_websocket_rejects_bad_token(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws/notifications?token=nope") as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4401


# ------------------------------------------------------------ dashboard files


def test_dashboard_served_with_spa_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.main import create_app

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root>APP</div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(get_settings(), "frontend_dist", str(tmp_path))
    with TestClient(create_app()) as c:
        assert c.get("/").text == "<div id=root>APP</div>"
        assert c.get("/leads/abc").text == "<div id=root>APP</div>"  # deep link
        js = c.get("/assets/app.js")
        assert js.text == "console.log(1)" and "immutable" in js.headers["cache-control"]
        assert c.get("/api/nope").status_code == 404  # API paths never fall back to the app
        assert c.get("/api/health").json()["status"] == "ok"
        # A file outside the dashboard folder must never be served, however the path is written.
        (tmp_path.parent / "secret.txt").write_text("TOP SECRET")
        for path in ("/%2e%2e/secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt", "/..%2fsecret.txt"):
            assert "TOP SECRET" not in c.get(path).text, path


def test_dashboard_not_built_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setattr(get_settings(), "frontend_dist", str(tmp_path / "missing"))
    with TestClient(create_app()) as c:
        assert "npm run build" in c.get("/").text


def test_alert_links_to_dashboard_lead_page(client: TestClient, inbox: dict) -> None:
    from app.db.models import Notification

    with session_scope() as s:
        n = s.scalar(select(Notification).where(Notification.lead_id == inbox["caller"]))
        assert f"/leads/{inbox['caller']}" in n.body


def test_messages_table_unchanged_for_inbound(inbox: dict) -> None:
    with session_scope() as s:
        assert (
            s.scalar(
                select(Message).where(Message.lead_id == inbox["ig"], Message.direction == "inbound")
            ).sender
            == "lead"
        )
