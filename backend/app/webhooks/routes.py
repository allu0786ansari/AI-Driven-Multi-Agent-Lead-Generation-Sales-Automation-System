"""Webhook endpoints, one per Meta webhook object (see docs/DECISIONS.md D-001).

Meta allows one callback URL per object type, so:

  /webhooks/meta/page       Page object: lead forms, Facebook comments, Messenger
  /webhooks/meta/instagram  Instagram object: comments, Instagram DMs
  /webhooks/whatsapp        WhatsApp Business Account: messages + delivery statuses

Each URL supports GET (Meta's verification handshake) and POST (events).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.webhooks.ingest import ingest_webhook
from app.webhooks.signature import SIGNATURE_HEADER, is_valid_signature

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

ENDPOINTS = {
    "meta_page": "/meta/page",
    "meta_instagram": "/meta/instagram",
    "whatsapp": "/whatsapp",
}


def _verify_handshake(mode: str | None, token: str | None, challenge: str | None) -> PlainTextResponse:
    settings = get_settings()
    if mode == "subscribe" and token and token == settings.meta_verify_token and challenge:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed")


async def _receive(request: Request, endpoint_name: str, db: Session) -> dict[str, Any]:
    settings = get_settings()
    body = await request.body()

    if settings.meta_verify_signatures and not is_valid_signature(
        body, request.headers.get(SIGNATURE_HEADER), settings.meta_app_secret
    ):
        log.warning("webhook rejected: bad signature", extra={"endpoint": endpoint_name})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body must be a JSON object")

    result = await run_in_threadpool(ingest_webhook, db, endpoint_name, payload)

    worker = getattr(request.app.state, "worker", None)
    if worker is not None and result.stored:
        worker.wake()

    return {"stored": result.stored, "duplicates": result.duplicates, "skipped": result.skipped}


def _make_routes(endpoint_name: str, path: str) -> None:
    @router.get(path, response_class=PlainTextResponse, name=f"{endpoint_name}_verify")
    def verify(
        mode: Annotated[str | None, Query(alias="hub.mode")] = None,
        token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
        challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
    ) -> PlainTextResponse:
        return _verify_handshake(mode, token, challenge)

    @router.post(path, name=f"{endpoint_name}_receive")
    async def receive(request: Request, db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
        return await _receive(request, endpoint_name, db)


for _name, _path in ENDPOINTS.items():
    _make_routes(_name, _path)
