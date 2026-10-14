"""WhatsApp Cloud API sender (live) and a fake that records sends (sandbox/tests).

Only the outbox (app/messaging/outbox.py) calls these - every message passes
the Send Gate first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.integrations.errors import ExternalAPIError
from app.integrations.graph_http import request


@dataclass(frozen=True)
class SendReceipt:
    platform_message_id: str


class WhatsAppSender(Protocol):
    def send_text(self, to: str, body: str) -> SendReceipt: ...

    def send_template(self, to: str, name: str, language: str, body_params: list[str]) -> SendReceipt: ...


def wa_number(phone_e164: str) -> str:
    """WhatsApp wants digits only, with country code and no '+'."""
    return phone_e164.lstrip("+")


class CloudAPIWhatsAppSender:
    def __init__(
        self,
        base_url: str,
        version: str,
        phone_number_id: str,
        access_token: str,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not phone_number_id or not access_token:
            raise ValueError("WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_ACCESS_TOKEN must be set for live mode")
        self._url = f"{base_url.rstrip('/')}/{version}/{phone_number_id}/messages"
        self._http = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    def _post(self, body: dict[str, Any]) -> SendReceipt:
        data = request(self._http, "POST", self._url, "WhatsApp send", json=body)
        messages = data.get("messages") or []
        if not messages or not messages[0].get("id"):
            raise ExternalAPIError(f"WhatsApp send returned no message id: {data}", transient=False)
        return SendReceipt(messages[0]["id"])

    def send_text(self, to: str, body: str) -> SendReceipt:
        return self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": wa_number(to),
                "type": "text",
                "text": {"body": body, "preview_url": False},
            }
        )

    def send_template(self, to: str, name: str, language: str, body_params: list[str]) -> SendReceipt:
        template: dict[str, Any] = {"name": name, "language": {"code": language}}
        if body_params:
            template["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in body_params]}
            ]
        return self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": wa_number(to),
                "type": "template",
                "template": template,
            }
        )


@dataclass
class FakeWhatsAppSender:
    """Records every send. Set `fail_next` to an ExternalAPIError to simulate a failure."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    fail_next: list[ExternalAPIError] = field(default_factory=list)

    def _record(self, item: dict[str, Any]) -> SendReceipt:
        if self.fail_next:
            raise self.fail_next.pop(0)
        receipt = SendReceipt(f"wamid.FAKE{uuid.uuid4().hex[:24]}")
        self.sent.append({**item, "platform_message_id": receipt.platform_message_id})
        return receipt

    def send_text(self, to: str, body: str) -> SendReceipt:
        return self._record({"type": "text", "to": wa_number(to), "body": body})

    def send_template(self, to: str, name: str, language: str, body_params: list[str]) -> SendReceipt:
        return self._record(
            {
                "type": "template",
                "to": wa_number(to),
                "name": name,
                "language": language,
                "params": body_params,
            }
        )
