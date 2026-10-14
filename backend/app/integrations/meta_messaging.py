"""Instagram and Messenger: private replies, DMs, public comment replies, hiding comments.

Endpoints (Graph API; check Meta's docs if your app uses Instagram Login):
  Private reply (one per comment)  POST /{page-or-ig-id}/messages  recipient={"comment_id": ...}
  DM within the 24h window         POST /{page-or-ig-id}/messages  recipient={"id": PSID/IGSID}
  Public reply  - Facebook         POST /{comment_id}/comments     message=...
                - Instagram        POST /{comment_id}/replies      message=...
  Hide comment  - Facebook         POST /{comment_id}              is_hidden=true
                - Instagram        POST /{comment_id}              hide=true

Send responses include `recipient_id` - for a private reply this is the
person's PSID/IGSID, which links the commenter to their later DMs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.db.enums import Channel
from app.integrations.errors import ExternalAPIError
from app.integrations.graph_http import request


@dataclass(frozen=True)
class MetaSendReceipt:
    platform_message_id: str
    recipient_id: str | None = None


class MetaMessagingClient(Protocol):
    def send_private_reply(self, channel: str, comment_id: str, text: str) -> MetaSendReceipt: ...

    def send_dm(self, channel: str, recipient_id: str, text: str) -> MetaSendReceipt: ...

    def reply_publicly(self, channel: str, comment_id: str, text: str) -> str: ...

    def hide_comment(self, channel: str, comment_id: str) -> None: ...


class LiveMetaMessagingClient:
    def __init__(
        self,
        graph_base_url: str,
        version: str,
        page_id: str,
        page_token: str,
        instagram_base_url: str,
        instagram_account_id: str,
        instagram_token: str,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not page_id or not page_token:
            raise ValueError("META_PAGE_ID and META_PAGE_ACCESS_TOKEN must be set for live mode")
        self._fb = f"{graph_base_url.rstrip('/')}/{version}"
        self._ig = f"{instagram_base_url.rstrip('/')}/{version}"
        self._ig_login = "graph.instagram.com" in instagram_base_url
        if self._ig_login and not instagram_account_id:
            raise ValueError("INSTAGRAM_ACCOUNT_ID is required with graph.instagram.com")
        self._page_id = page_id
        self._ig_id = instagram_account_id
        self._fb_http = httpx.Client(
            timeout=timeout_seconds, transport=transport, headers={"Authorization": f"Bearer {page_token}"}
        )
        self._ig_http = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={"Authorization": f"Bearer {instagram_token or page_token}"},
        )

    def _client(self, channel: str) -> tuple[httpx.Client, str, str]:
        """(http client, API base, ID to send messages from)."""
        if channel == Channel.INSTAGRAM:
            return self._ig_http, self._ig, (self._ig_id if self._ig_login else self._page_id)
        if channel == Channel.MESSENGER:
            return self._fb_http, self._fb, self._page_id
        raise ValueError(f"Unsupported channel {channel}")

    def _send(self, channel: str, recipient: dict[str, str], text: str, what: str) -> MetaSendReceipt:
        http, base, sender_id = self._client(channel)
        body: dict[str, Any] = {"recipient": recipient, "message": {"text": text}}
        if "id" in recipient:
            body["messaging_type"] = "RESPONSE"
        data = request(http, "POST", f"{base}/{sender_id}/messages", what, json=body)
        if not data.get("message_id"):
            raise ExternalAPIError(f"{what} returned no message_id: {data}", transient=False)
        return MetaSendReceipt(data["message_id"], data.get("recipient_id"))

    def send_private_reply(self, channel: str, comment_id: str, text: str) -> MetaSendReceipt:
        return self._send(channel, {"comment_id": comment_id}, text, f"{channel} private reply")

    def send_dm(self, channel: str, recipient_id: str, text: str) -> MetaSendReceipt:
        return self._send(channel, {"id": recipient_id}, text, f"{channel} message")

    def reply_publicly(self, channel: str, comment_id: str, text: str) -> str:
        http, base, _ = self._client(channel)
        edge = "replies" if channel == Channel.INSTAGRAM else "comments"
        data = request(
            http, "POST", f"{base}/{comment_id}/{edge}", "public comment reply", data={"message": text}
        )
        return str(data.get("id", ""))

    def hide_comment(self, channel: str, comment_id: str) -> None:
        http, base, _ = self._client(channel)
        field_name = "hide" if channel == Channel.INSTAGRAM else "is_hidden"
        request(http, "POST", f"{base}/{comment_id}", "hide comment", data={field_name: "true"})


@dataclass
class FakeMetaMessagingClient:
    """Records every call. `fail_next` simulates API errors for the next calls."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    public_replies: list[dict[str, Any]] = field(default_factory=list)
    hidden: list[dict[str, Any]] = field(default_factory=list)
    fail_next: list[ExternalAPIError] = field(default_factory=list)

    def _maybe_fail(self) -> None:
        if self.fail_next:
            raise self.fail_next.pop(0)

    def send_private_reply(self, channel: str, comment_id: str, text: str) -> MetaSendReceipt:
        self._maybe_fail()
        receipt = MetaSendReceipt(f"m_FAKE{uuid.uuid4().hex[:20]}", f"scoped-{comment_id}")
        self.sent.append(
            {"type": "private_reply", "channel": channel, "comment_id": comment_id, "text": text}
        )
        return receipt

    def send_dm(self, channel: str, recipient_id: str, text: str) -> MetaSendReceipt:
        self._maybe_fail()
        self.sent.append({"type": "dm", "channel": channel, "recipient_id": recipient_id, "text": text})
        return MetaSendReceipt(f"m_FAKE{uuid.uuid4().hex[:20]}", recipient_id)

    def reply_publicly(self, channel: str, comment_id: str, text: str) -> str:
        self._maybe_fail()
        self.public_replies.append({"channel": channel, "comment_id": comment_id, "text": text})
        return f"reply_{comment_id}"

    def hide_comment(self, channel: str, comment_id: str) -> None:
        self._maybe_fail()
        self.hidden.append({"channel": channel, "comment_id": comment_id})
