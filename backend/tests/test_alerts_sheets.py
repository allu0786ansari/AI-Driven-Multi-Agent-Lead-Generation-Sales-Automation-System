"""Salesperson alerts (log / email / Telegram) and the Sheets mirror (CSV / Google)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.parse import unquote

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.alerts import notify as notify_mod
from app.alerts.notify import deliver_notifications, notify
from app.alerts.senders import AlertSendError, EmailAlertSender, FakeAlertSender, TelegramAlertSender
from app.db.enums import NotificationKind
from app.db.models import Lead, Notification
from app.db.session import session_scope
from app.integrations.errors import ExternalAPIError
from app.sheets import mirror
from app.sheets.mirror import CsvWriter, GoogleSheetsWriter, build_rows, sync_all, sync_user


def _lead(user_id: str, **kw) -> str:  # noqa: ANN003
    with session_scope() as s:
        lead = Lead(
            user_id=user_id,
            name="Priya",
            contact_phone="+919876543210",
            qualify_slots={"city": "Chennai"},
            **kw,
        )
        s.add(lead)
        s.flush()
        return lead.lead_id


def _notify(lead_id: str, key: str = "k1") -> None:
    with session_scope() as s:
        notify(s, s.get(Lead, lead_id), NotificationKind.HOT_LEAD, "Lead is hot.", key, "need it today")


def _notes() -> list[Notification]:
    with session_scope() as s:
        return list(s.scalars(select(Notification)))


def test_notify_is_idempotent_and_body_has_details(user) -> None:  # noqa: ANN001
    lead_id = _lead(user.user_id, qualification_score=82, status="hot")
    _notify(lead_id)
    _notify(lead_id)
    [n] = _notes()
    assert n.title == "🔥 Hot lead: Priya (+919876543210)"
    assert (
        "Score: 82 (hot)" in n.body
        and "City: Chennai" in n.body
        and 'Last message: "need it today"' in n.body
    )


def test_log_mode_marks_logged(user) -> None:  # noqa: ANN001
    _notify(_lead(user.user_id))
    assert deliver_notifications() == {"logged": 1}
    assert deliver_notifications() == {}


def test_live_mode_sends_to_all_channels_and_retries_failures(user, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    email, telegram = FakeAlertSender("email"), FakeAlertSender("telegram", fail=True)
    monkeypatch.setattr(notify_mod, "get_alert_senders", lambda: (email, telegram))
    _notify(_lead(user.user_id))
    assert deliver_notifications() == {"queued": 1}  # telegram failed -> retry later
    [n] = _notes()
    assert n.channels_sent == ["email"] and "telegram" in n.error
    telegram.fail = False
    with session_scope() as s:
        s.get(Notification, n.notification_id).next_attempt_at = n.created_at
    assert deliver_notifications() == {"sent": 1}
    assert len(email.sent) == 1 and len(telegram.sent) == 1  # email not sent twice


def test_telegram_sender_request() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    TelegramAlertSender("https://tg.test", "TOKEN", "42", transport=httpx.MockTransport(handler)).send(
        "T", "B"
    )
    assert seen["url"] == "https://tg.test/botTOKEN/sendMessage"
    assert seen["body"]["chat_id"] == "42" and seen["body"]["text"] == "T\n\nB"
    bad = TelegramAlertSender(
        "https://tg.test",
        "T",
        "1",
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"ok": False})),
    )
    with pytest.raises(AlertSendError):
        bad.send("T", "B")


def test_email_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):  # noqa: ANN001
            sent["server"] = (host, port)

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *a):  # noqa: ANN002, ANN204
            return False

        def starttls(self) -> None:
            sent["tls"] = True

        def login(self, u, p) -> None:  # noqa: ANN001
            sent["login"] = u

        def send_message(self, msg) -> None:  # noqa: ANN001
            sent["msg"] = msg

    monkeypatch.setattr("app.alerts.senders.smtplib.SMTP", FakeSMTP)
    EmailAlertSender("smtp.test", 587, "me@x.com", "pw", True, "", "boss@x.com").send("Hot lead", "Body")
    assert sent["server"] == ("smtp.test", 587) and sent["tls"] and sent["login"] == "me@x.com"
    assert sent["msg"]["To"] == "boss@x.com" and sent["msg"]["Subject"] == "Hot lead"


# --------------------------------------------------------------------- sheets


def test_build_rows_and_csv_mirror(user, tmp_path: Path) -> None:  # noqa: ANN001
    _lead(user.user_id, status="hot", qualification_score=80, conversation_summary="Wants: Kettle")
    with session_scope() as s:
        rows = build_rows(s, user.user_id)
    assert rows[0][:3] == ["Lead ID", "Name", "Phone"] and rows[1][1] == "Priya"
    out = tmp_path / "mirror.csv"
    writer = CsvWriter(str(out))
    with session_scope() as s:
        assert sync_user(s, user.user_id, writer) is True
    with session_scope() as s:
        assert sync_user(s, user.user_id, writer) is False  # nothing changed -> no write
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM so Excel reads UTF-8
    with out.open(encoding="utf-8-sig", newline="") as fh:
        data = list(csv.reader(fh))
    assert data[1][data[0].index("Status")] == "hot" and data[1][data[0].index("Score")] == "80"


def test_locked_csv_is_retried_not_crashing(user, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    _lead(user.user_id)

    def locked(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise PermissionError("in use")

    monkeypatch.setattr(mirror.os, "replace", locked)
    assert sync_all(CsvWriter(str(tmp_path / "m.csv"))) == 0  # logged, not raised
    monkeypatch.undo()
    assert sync_all(CsvWriter(str(tmp_path / "m.csv"))) == 1  # state was not saved, so it retries


@pytest.fixture
def service_account(tmp_path: Path) -> tuple[Path, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    path = tmp_path / "sa.json"
    path.write_text(
        json.dumps(
            {
                "client_email": "bot@proj.iam.gserviceaccount.com",
                "private_key": pem.decode(),
                "token_uri": "https://oauth.test/token",
            }
        )
    )
    return path, key


def test_google_writer_auth_write_and_clear(service_account) -> None:  # noqa: ANN001
    path, key = service_account
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth.test":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            claims = jwt.decode(
                unquote(form["assertion"]),
                key.public_key(),
                algorithms=["RS256"],
                audience="https://oauth.test/token",
            )
            assert claims["iss"] == "bot@proj.iam.gserviceaccount.com" and "spreadsheets" in claims["scope"]
            calls.append(("token", "", {}))
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer AT"
        calls.append(
            (
                request.method,
                unquote(request.url.path),
                json.loads(request.content) if request.content else {},
            )
        )
        return httpx.Response(200, json={})

    w = GoogleSheetsWriter(str(path), "SHEET1", "Leads", transport=httpx.MockTransport(handler))
    w.write([["h"], ["a"]], previous_row_count=0)
    w.write([["h"]], previous_row_count=2)
    assert [c[0] for c in calls] == ["token", "PUT", "PUT", "POST"]  # token fetched once, reused
    assert calls[1][1] == "/v4/spreadsheets/SHEET1/values/'Leads'!A1" and calls[1][2]["values"] == [
        ["h"],
        ["a"],
    ]
    assert calls[3][1] == "/v4/spreadsheets/SHEET1/values/'Leads'!A2:Z2:clear"


def test_google_writer_errors(service_account) -> None:  # noqa: ANN001
    path, _ = service_account
    w = GoogleSheetsWriter(
        str(path),
        "S",
        "Leads",
        transport=httpx.MockTransport(lambda r: httpx.Response(403, text="no access")),
    )
    with pytest.raises(ExternalAPIError, match="403"):
        w.write([["h"]], 0)
    with pytest.raises(ValueError):
        GoogleSheetsWriter("", "S", "Leads")
