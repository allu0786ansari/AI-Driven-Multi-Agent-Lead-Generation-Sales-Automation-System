"""Qualify Agent (proposal Section 3c): update what we know about a lead after each message,
score it, and decide the next question.

spec 3.3: qualify_agent.process_message(lead_id, message) -> QualifyResult
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.enums import Direction, LeadStatus
from app.db.models import Lead, Message, Product
from app.meta_feedback import queue_stage
from app.qualify.config import get_config
from app.qualify.scoring import ScoreResult, next_question, score, summarize
from app.qualify.slots import QualifySlots, extract_rules
from app.rag.retrieve import RetrievedProduct, mentions_product

log = logging.getLogger(__name__)

# Statuses the agent may change. Once a salesperson sets contacted / converted / lost, it stays.
AUTO_STATUSES = {LeadStatus.NEW, LeadStatus.HOT, LeadStatus.WARM, LeadStatus.COLD}
CLEAR_WINNER_MARGIN = 0.05  # top search hit must beat the runner-up by this much to count as "the product"


@dataclass
class QualifyResult:
    slots: QualifySlots
    score: ScoreResult
    status: str
    became_hot: bool
    next_question: str | None


def current_slots(lead: Lead) -> QualifySlots:
    try:
        return QualifySlots.model_validate(lead.qualify_slots or {})
    except ValueError:
        log.warning("stored qualify_slots invalid; starting fresh", extra={"lead_id": lead.lead_id})
        return QualifySlots()


def _inbound_count(session: Session, lead: Lead) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.lead_id == lead.lead_id,
                Message.direction == Direction.INBOUND,
                Message.media_type != "comment",
            )
        )
        or 0
    )


def _reference_price(session: Session, lead: Lead, slots: QualifySlots) -> Decimal | None:
    """Cheapest price among products matching what the lead wants."""
    q = select(func.min(Product.price)).where(Product.user_id == lead.user_id)
    if slots.product:
        q = q.where((Product.name == slots.product) | (Product.sku == slots.product))
    elif slots.category:
        q = q.where(Product.category == slots.category)
    else:
        return None
    value = session.scalar(q)
    return Decimal(str(value)) if value is not None else None


def product_from_hits(text: str, hits: list[RetrievedProduct] | None) -> QualifySlots:
    """What the customer is asking about, from the product search.

    The category counts when a word of the message names the top product or its
    category ("fryer", "kettle", "mixie" -> no). The exact product counts only
    when it clearly beats the next result, e.g. "6 litre air fryer" vs "air fryer".
    """
    if not hits:
        return QualifySlots()
    top = hits[0]
    if not mentions_product(text, top.product):
        return QualifySlots()
    clear = (
        len(hits) == 1
        or top.score - hits[1].score >= CLEAR_WINNER_MARGIN
        or hits[1].product.category != top.product.category
    )
    return QualifySlots(product=top.product.name if clear else None, category=top.product.category)


_COUNT_THEN_WORD = re.compile(r"\b([1-9]\d?)\s+([^\W\d_]{3,})", re.UNICODE)


def quantity_from_hits(text: str, hits: list[RetrievedProduct] | None) -> QualifySlots:
    """ "2 mixer", "3 air fryers": a small number right before a word naming a found product.

    Found in the Week 5 evaluation. Numbers above 99 are never read as a quantity
    (prices, watts), nor numbers before other words ("1000 watt", "5000 ka").
    """
    for m in _COUNT_THEN_WORD.finditer(text or ""):
        word = m.group(2)
        if any(mentions_product(word, h.product) for h in hits or []):
            return QualifySlots(quantity=int(m.group(1)))
    return QualifySlots()


def process_message(
    session: Session,
    lead: Lead,
    text: str,
    llm_slots: QualifySlots | None = None,
    hits: list[RetrievedProduct] | None = None,
) -> QualifyResult:
    config = get_config(session, lead.user_id)

    found = (
        quantity_from_hits(text, hits)
        .merged_with(extract_rules(text))
        .merged_with(product_from_hits(text, hits))
    )
    if llm_slots is not None:
        found = found.merged_with(llm_slots)  # the LLM's reading wins over the rules
    slots = current_slots(lead).merged_with(found)

    result = score(slots, config, _inbound_count(session, lead), _reference_price(session, lead, slots))
    old_status = lead.status
    if old_status in AUTO_STATUSES:
        lead.status = result.status
    became_hot = lead.status == LeadStatus.HOT and old_status != LeadStatus.HOT
    if became_hot:
        queue_stage(session, lead, "qualified")  # tell Meta (Week 6, when enabled)

    question = next_question(slots, config)
    lead.qualify_slots = slots.model_dump(mode="json")
    lead.qualification_score = result.score
    lead.score_rationale = result.rationale
    lead.conversation_summary = summarize(slots)
    lead.product_interest = slots.product or slots.category or lead.product_interest
    lead.qualify_state = "complete" if question is None else "in_progress"
    log.info("lead scored", extra={"lead_id": lead.lead_id, "score": result.score, "status": lead.status})
    return QualifyResult(slots, result, lead.status, became_hot, question)


def preview_next_question(
    session: Session, lead: Lead, text: str, hits: list[RetrievedProduct] | None = None
) -> str | None:
    """The question the reply should end with, counting what this message already answers.

    Uses the rules and the product search (the LLM's reading arrives together with the reply).
    """
    found = (
        quantity_from_hits(text, hits)
        .merged_with(extract_rules(text))
        .merged_with(product_from_hits(text, hits))
    )
    slots = current_slots(lead).merged_with(found)
    return next_question(slots, get_config(session, lead.user_id))
