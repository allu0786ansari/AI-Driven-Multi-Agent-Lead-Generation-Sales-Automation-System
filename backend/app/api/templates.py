"""WhatsApp message templates available to send from the dashboard (spec 3.2: GET /api/templates).

Live mode with WHATSAPP_BUSINESS_ACCOUNT_ID set: read from Meta, with approval status.
Otherwise: the templates this system uses (from settings), status "not checked".
"""

from __future__ import annotations

import re

import httpx
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.auth.deps import CurrentUser
from app.config import get_settings
from app.integrations.errors import ExternalAPIError
from app.integrations.graph_http import request

router = APIRouter(prefix="/api", tags=["templates"])

_PARAM = re.compile(r"\{\{\s*(\d+)\s*\}\}")


class TemplateOut(BaseModel):
    name: str
    language: str
    category: str  # marketing | utility | authentication
    status: str  # APPROVED | PENDING | REJECTED | ... | not checked
    body: str | None = None
    param_count: int = 0


class TemplatesResponse(BaseModel):
    source: str  # meta | settings
    templates: list[TemplateOut]
    error: str | None = None


# Bodies from docs/meta_setup.md (what we asked Meta to approve).
KNOWN_BODIES = {
    "lead_form_welcome": (
        "Hi {{1}}, thanks for your interest in {{2}}! I'm the assistant for <Business Name>. "
        "I can share prices, offers and delivery details right here on WhatsApp. What would you like to know?"
    ),
    "nurture_followup_1": "Hi {{1}}, just checking in about {{2}}. Do you have any questions I can answer?",
    "nurture_offer": "Hi {{1}}, {{2}} is still available and we have a special offer this week: {{3}}.",
    "nurture_last_check": (
        "Hi {{1}}, this is our last reminder about {{2}}. If you're still interested, just reply."
    ),
}


def _count_params(body: str | None) -> int:
    return len(set(_PARAM.findall(body or "")))


def _from_settings() -> list[TemplateOut]:
    s = get_settings()
    names = [s.template_lead_form_welcome, "nurture_followup_1", "nurture_offer", "nurture_last_check"]
    return [
        TemplateOut(
            name=n,
            language=s.template_language,
            category="marketing",
            status="not checked",
            body=KNOWN_BODIES.get(n),
            param_count=_count_params(KNOWN_BODIES.get(n)),
        )
        for n in dict.fromkeys(names)
    ]


def _from_meta() -> list[TemplateOut]:
    s = get_settings()
    base = f"{s.meta_graph_base_url.rstrip('/')}/{s.meta_graph_version}"
    url = f"{base}/{s.whatsapp_business_account_id}/message_templates"
    with httpx.Client(
        timeout=s.meta_api_timeout_seconds, headers={"Authorization": f"Bearer {s.whatsapp_access_token}"}
    ) as http:
        data = request(
            http,
            "GET",
            url,
            "List templates",
            params={"fields": "name,status,category,language,components", "limit": 100},
        )
    out = []
    for t in data.get("data", []):
        body = next((c.get("text") for c in t.get("components", []) if c.get("type") == "BODY"), None)
        out.append(
            TemplateOut(
                name=t["name"],
                language=t.get("language", ""),
                category=str(t.get("category", "")).lower(),
                status=t.get("status", ""),
                body=body,
                param_count=_count_params(body),
            )
        )
    return out


@router.get("/templates", response_model=TemplatesResponse)
async def list_templates(user: CurrentUser) -> TemplatesResponse:  # noqa: ARG001
    s = get_settings()
    if s.meta_api_mode == "live" and s.whatsapp_business_account_id and s.whatsapp_access_token:
        try:
            return TemplatesResponse(source="meta", templates=await run_in_threadpool(_from_meta))
        except ExternalAPIError as exc:
            return TemplatesResponse(source="settings", templates=_from_settings(), error=str(exc))
    return TemplatesResponse(source="settings", templates=_from_settings())
