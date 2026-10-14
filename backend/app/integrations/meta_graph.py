"""Meta Graph API client for reading lead form submissions.

Meta's `leadgen` webhook only carries IDs; the answers are fetched with
GET /{leadgen_id} (needs the `leads_retrieval` permission and a Page token).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.integrations.graph_http import request

LEAD_FIELDS = (
    "id,created_time,ad_id,adset_id,campaign_id,form_id,platform,field_data,custom_disclaimer_responses"
)


class MetaGraphClient(Protocol):
    def fetch_lead(self, leadgen_id: str) -> dict[str, Any]: ...


class LiveMetaGraphClient:
    def __init__(
        self,
        base_url: str,
        version: str,
        page_access_token: str,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not page_access_token:
            raise ValueError("META_PAGE_ACCESS_TOKEN must be set for live mode")
        self._base = f"{base_url.rstrip('/')}/{version}"
        self._http = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={"Authorization": f"Bearer {page_access_token}"},
        )

    def fetch_lead(self, leadgen_id: str) -> dict[str, Any]:
        return request(
            self._http, "GET", f"{self._base}/{leadgen_id}", "Lead fetch", params={"fields": LEAD_FIELDS}
        )


@dataclass
class FakeMetaGraphClient:
    """Returns believable lead data for any leadgen_id (or a registered override)."""

    leads: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def fetch_lead(self, leadgen_id: str) -> dict[str, Any]:
        self.calls.append(leadgen_id)
        if leadgen_id in self.leads:
            return self.leads[leadgen_id]
        digits = "".join(c for c in leadgen_id if c.isdigit()).rjust(8, "0")[-8:]
        return {
            "id": leadgen_id,
            "created_time": "2026-10-14T10:00:00+0000",
            "ad_id": "120200000000000002",
            "campaign_id": "120200000000000009",
            "form_id": "400000000000001",
            "field_data": [
                {"name": "full_name", "values": [f"Form Lead {digits[-4:]}"]},
                {"name": "phone_number", "values": [f"+9198{digits}"]},
                {"name": "email", "values": [f"lead{digits[-4:]}@example.com"]},
                {"name": "product_interest", "values": ["Air Fryers"]},
                # IDs starting with "000" simulate a lead who did not tick the WhatsApp box
                {"name": "whatsapp_opt_in", "values": ["no" if leadgen_id.startswith("000") else "yes"]},
            ],
        }
