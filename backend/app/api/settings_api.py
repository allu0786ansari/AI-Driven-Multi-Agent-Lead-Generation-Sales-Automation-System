"""Business settings: qualification rules and post/ad -> product links."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.catalog.links import ContentLinks, get_links, save_links
from app.db.session import get_db
from app.qualify.config import QualificationConfig, get_config, save_config

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/qualification", response_model=QualificationConfig)
def get_qualification(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> QualificationConfig:
    """Qualifying questions (asked in this order), scoring weights and hot/warm thresholds."""
    return get_config(db, user.user_id)


@router.put("/qualification", response_model=QualificationConfig)
def put_qualification(
    config: QualificationConfig, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> QualificationConfig:
    """Replace the qualification rules. Applies to the next message each lead sends."""
    save_config(db, user.user_id, config)
    db.commit()
    return config


@router.get("/content-products", response_model=ContentLinks)
def get_content_products(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> ContentLinks:
    """Which product each post / reel / ad shows: {"links": {"<content id>": "<SKU>"}}."""
    return get_links(db, user.user_id)


@router.put("/content-products", response_model=ContentLinks)
def put_content_products(
    links: ContentLinks, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> ContentLinks:
    """Replace all links. Content IDs: Instagram media ID, Facebook post ID, or Click-to-WhatsApp ad ID."""
    unknown = save_links(db, user.user_id, links)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown SKU(s): {', '.join(unknown)}")
    db.commit()
    return links
