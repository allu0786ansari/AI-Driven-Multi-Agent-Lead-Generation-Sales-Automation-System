"""Which product a post, reel or ad shows (D-018).

"How much is this?" under an ad only makes sense if we know what the ad
shows. The business links content IDs to SKUs (PUT /api/settings/content-products):

  Instagram post/reel: the media ID from the comment webhook
  Facebook post:       the post ID ("<page id>_<post id>")
  Click-to-WhatsApp:   the ad ID from the message referral

The IDs appear in each lead's source details and in /api/events notes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BusinessSetting, Lead, Product
from app.qualify.agent import current_slots
from app.qualify.slots import QualifySlots
from app.rag.records import ProductRecord

SETTINGS_KEY = "content_products"


class ContentLinks(BaseModel):
    links: dict[str, str] = Field(default_factory=dict, description="content ID -> product SKU")


def get_links(session: Session, user_id: str) -> ContentLinks:
    row = session.get(BusinessSetting, (user_id, SETTINGS_KEY))
    return ContentLinks.model_validate(row.value) if row else ContentLinks()


def save_links(session: Session, user_id: str, links: ContentLinks) -> list[str]:
    """Saves the links; returns SKUs that do not exist in the catalog (nothing saved then)."""
    skus = set(session.scalars(select(Product.sku).where(Product.user_id == user_id)))
    unknown = sorted({sku for sku in links.links.values() if sku not in skus})
    if unknown:
        return unknown
    row = session.get(BusinessSetting, (user_id, SETTINGS_KEY))
    value = links.model_dump()
    if row is None:
        session.add(BusinessSetting(user_id=user_id, key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    session.flush()
    return []


def linked_product(session: Session, user_id: str, content_id: str | None) -> ProductRecord | None:
    if not content_id:
        return None
    sku = get_links(session, user_id).links.get(str(content_id))
    if not sku:
        return None
    row = session.scalar(select(Product).where(Product.user_id == user_id, Product.sku == sku))
    return ProductRecord.from_row(row) if row else None


def apply_link(lead: Lead, product: ProductRecord) -> None:
    """Record that the lead is asking about this product (unless they already named another)."""
    slots = current_slots(lead)
    if not (slots.product or slots.category):
        lead.qualify_slots = slots.merged_with(
            QualifySlots(product=product.name, category=product.category)
        ).model_dump(mode="json")
    lead.product_interest = lead.product_interest or product.name
