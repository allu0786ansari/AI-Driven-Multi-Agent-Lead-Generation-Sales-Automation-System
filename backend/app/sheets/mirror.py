"""Read-only mirror of the lead table for the salesperson (spec Section 4.6).

The database stays the source of truth; this copy is rewritten as a whole,
at most once per SHEETS_SYNC_SECONDS and only when something changed, so
Google's write limits are never a concern.

SHEETS_MODE:
  off     - no mirror
  csv     - backend/data/leads_mirror.csv (opens in Excel; default)
  google  - a Google Sheet, written with a service account
            (share the sheet with the service account's email as Editor)
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import BusinessSetting, Lead, User
from app.db.session import session_scope
from app.integrations.errors import ExternalAPIError

log = logging.getLogger(__name__)

STATE_KEY = "sheets_sync"
COLUMNS = [
    ("lead_id", "Lead ID"),
    ("name", "Name"),
    ("contact_phone", "Phone"),
    ("contact_email", "Email"),
    ("instagram_scoped_id", "Instagram ID"),
    ("preferred_channel", "Channel"),
    ("source", "Source"),
    ("source_detail", "Source detail"),
    ("ad_id", "Ad ID"),
    ("campaign_id", "Campaign ID"),
    ("product_interest", "Product interest"),
    ("qualification_score", "Score"),
    ("status", "Status"),
    ("conversation_summary", "Summary"),
    ("score_rationale", "Why this score"),
    ("assigned_to", "Assigned to"),
    ("is_human_takeover", "Human handling"),
    ("whatsapp_opt_in", "WhatsApp opt-in"),
    ("opted_out", "Opted out"),
    ("messaging_cost_estimate", "Messaging cost (INR)"),
    ("created_at", "Created"),
    ("last_activity_at", "Last activity"),
]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


def build_rows(session: Session, user_id: str) -> list[list[str]]:
    leads = session.scalars(
        select(Lead).where(Lead.user_id == user_id).order_by(Lead.last_activity_at.desc())
    ).all()
    rows = [[title for _, title in COLUMNS]]
    rows += [[_cell(getattr(lead, attr)) for attr, _ in COLUMNS] for lead in leads]
    return rows


class SheetsWriter(Protocol):
    def write(self, rows: list[list[str]], previous_row_count: int) -> None: ...


class CsvWriter:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def write(self, rows: list[list[str]], previous_row_count: int) -> None:  # noqa: ARG002
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            # utf-8-sig so Excel shows ₹ and non-English names correctly
            with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as fh:
                csv.writer(fh).writerows(rows)
            os.replace(tmp, self.path)
        except PermissionError as exc:
            Path(tmp).unlink(missing_ok=True)
            # On Windows the file is locked while open in Excel; try again next cycle.
            raise ExternalAPIError(
                f"{self.path} is open in another program; close it", transient=True
            ) from exc
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


class GoogleSheetsWriter:
    """Writes with the Sheets REST API using a service-account key (no extra Google libraries)."""

    SCOPE = "https://www.googleapis.com/auth/spreadsheets"
    API = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(
        self, key_file: str, sheet_id: str, tab: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not key_file or not sheet_id:
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_FILE and GOOGLE_SHEET_ID are required for SHEETS_MODE=google"
            )
        self._key = json.loads(Path(key_file).read_text(encoding="utf-8"))
        self._sheet_id = sheet_id
        self._tab = tab
        self._http = httpx.Client(timeout=30, transport=transport)
        self._token: str | None = None
        self._token_expires = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        now = int(time.time())
        token_uri = self._key.get("token_uri", "https://oauth2.googleapis.com/token")
        assertion = jwt.encode(
            {
                "iss": self._key["client_email"],
                "scope": self.SCOPE,
                "aud": token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            self._key["private_key"],
            algorithm="RS256",
        )
        try:
            resp = self._http.post(
                token_uri,
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            )
        except httpx.HTTPError as exc:
            raise ExternalAPIError(f"Google token request failed: {exc}", transient=True) from exc
        if resp.status_code != 200:
            raise ExternalAPIError(
                f"Google token error {resp.status_code}: {resp.text[:200]}", transient=False
            )
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + int(data.get("expires_in", 3600))
        return self._token

    def _call(self, method: str, url: str, **kwargs: Any) -> None:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        try:
            resp = self._http.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ExternalAPIError(f"Google Sheets call failed: {exc}", transient=True) from exc
        if resp.status_code != 200:
            transient = resp.status_code >= 500 or resp.status_code == 429
            raise ExternalAPIError(
                f"Google Sheets error {resp.status_code}: {resp.text[:300]}", transient=transient
            )

    def write(self, rows: list[list[str]], previous_row_count: int) -> None:
        target = quote(f"'{self._tab}'!A1", safe="")
        self._call(
            "PUT",
            f"{self.API}/{self._sheet_id}/values/{target}",
            params={"valueInputOption": "RAW"},
            json={"majorDimension": "ROWS", "values": rows},
        )
        if previous_row_count > len(rows):  # leads were removed: clear leftover rows
            stale = quote(f"'{self._tab}'!A{len(rows) + 1}:Z{previous_row_count}", safe="")
            self._call("POST", f"{self.API}/{self._sheet_id}/values/{stale}:clear")


def get_writer() -> SheetsWriter | None:
    s = get_settings()
    if s.sheets_mode == "csv":
        return CsvWriter(s.sheets_csv_path)
    if s.sheets_mode == "google":
        return GoogleSheetsWriter(s.google_service_account_file, s.google_sheet_id, s.google_sheet_tab)
    return None


def sync_user(session: Session, user_id: str, writer: SheetsWriter) -> bool:
    """Write the mirror if the rows changed since the last successful write. Returns True if written."""
    rows = build_rows(session, user_id)
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    state = session.get(BusinessSetting, (user_id, STATE_KEY))
    previous = state.value if state else {}
    if previous.get("hash") == digest:
        return False
    writer.write(rows, int(previous.get("rows", 0)))
    value = {"hash": digest, "rows": len(rows), "synced_rows": len(rows) - 1}
    if state is None:
        session.add(BusinessSetting(user_id=user_id, key=STATE_KEY, value=value))
    else:
        state.value = value
    log.info("sheets mirror updated", extra={"leads": len(rows) - 1, "mode": get_settings().sheets_mode})
    return True


def sync_all(writer: SheetsWriter | None = None) -> int:
    """Sync every business. Errors are logged and retried on the next cycle."""
    writer = writer or get_writer()
    if writer is None:
        return 0
    written = 0
    with session_scope() as s:
        for user_id in s.scalars(select(User.user_id)).all():
            try:
                written += sync_user(s, user_id, writer)
            except ExternalAPIError as exc:
                log.warning("sheets mirror failed; will retry", extra={"error": str(exc)[:300]})
    return written
