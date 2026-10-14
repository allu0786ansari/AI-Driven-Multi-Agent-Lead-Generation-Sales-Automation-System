"""Where salesperson alerts go: email (SMTP) and/or Telegram.

Alerts deliberately do NOT go over WhatsApp: a business-initiated WhatsApp
message to the salesperson would need a paid template (proposal Section 3c).
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from functools import lru_cache
from typing import Protocol

import httpx

from app.config import get_settings


class AlertSendError(RuntimeError):
    pass


class AlertSender(Protocol):
    name: str

    def send(self, title: str, body: str) -> None: ...


class EmailAlertSender:
    name = "email"

    def __init__(
        self, host: str, port: int, username: str, password: str, starttls: bool, sender: str, to: str
    ) -> None:
        if not host or not to:
            raise ValueError("SMTP_HOST and ALERT_EMAIL_TO are required for email alerts")
        self.host, self.port, self.username, self.password = host, port, username, password
        self.starttls, self.sender, self.to = starttls, sender or username, to

    def send(self, title: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = self.sender
        msg["To"] = self.to
        msg.set_content(body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
                if self.starttls:
                    smtp.starttls()
                if self.username:
                    smtp.login(self.username, self.password)
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            raise AlertSendError(f"email: {exc}") from exc


class TelegramAlertSender:
    name = "telegram"

    def __init__(
        self, base_url: str, bot_token: str, chat_id: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not bot_token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required for Telegram alerts")
        self._url = f"{base_url.rstrip('/')}/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._http = httpx.Client(timeout=20, transport=transport)

    def send(self, title: str, body: str) -> None:
        try:
            resp = self._http.post(
                self._url,
                json={
                    "chat_id": self._chat_id,
                    "text": f"{title}\n\n{body}",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.HTTPError as exc:
            raise AlertSendError(f"telegram: {exc}") from exc
        if resp.status_code != 200 or not resp.json().get("ok"):
            raise AlertSendError(f"telegram: {resp.status_code} {resp.text[:200]}")


@dataclass
class FakeAlertSender:
    name: str
    sent: list[tuple[str, str]] = field(default_factory=list)
    fail: bool = False

    def send(self, title: str, body: str) -> None:
        if self.fail:
            raise AlertSendError(f"{self.name}: simulated failure")
        self.sent.append((title, body))


@lru_cache
def get_alert_senders() -> tuple[AlertSender, ...]:
    """Configured senders for ALERTS_MODE=live; empty in log mode."""
    s = get_settings()
    if s.alerts_mode != "live":
        return ()
    senders: list[AlertSender] = []
    if "email" in s.alert_channels:
        senders.append(
            EmailAlertSender(
                s.smtp_host,
                s.smtp_port,
                s.smtp_username,
                s.smtp_password,
                s.smtp_starttls,
                s.alert_email_from,
                s.alert_email_to,
            )  # fmt: skip
        )
    if "telegram" in s.alert_channels:
        senders.append(TelegramAlertSender(s.telegram_api_base_url, s.telegram_bot_token, s.telegram_chat_id))
    return tuple(senders)
