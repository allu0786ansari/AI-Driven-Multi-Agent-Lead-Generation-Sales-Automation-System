"""Which business (user_id) an event or request belongs to.

The prototype serves one business, so every webhook event is assigned to that
business. When multi-tenancy arrives (spec Section 8), this function will look
up the tenant by the Meta Page ID / Instagram account ID / WhatsApp phone
number ID found in the event, using the `connections` table.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import User


def resolve_user_id(session: Session, payload: dict | None = None) -> str | None:  # noqa: ARG001
    configured = get_settings().default_user_id
    if configured:
        return configured
    return session.scalar(select(User.user_id).order_by(User.created_at).limit(1))
