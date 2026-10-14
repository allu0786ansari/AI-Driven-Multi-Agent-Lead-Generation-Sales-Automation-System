"""Keeps product vectors in sync with the catalog.

Only products whose searchable text changed (or that were embedded with a
different model) are re-embedded, so editing one product re-embeds only that
product (spec Section 3.2).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import Product, ProductEmbedding
from app.rag.embeddings import EmbeddingClient
from app.rag.records import ProductRecord

log = logging.getLogger(__name__)


@dataclass
class IndexResult:
    embedded: int = 0
    unchanged: int = 0


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def index_products(session: Session, user_id: str, client: EmbeddingClient) -> IndexResult:
    result = IndexResult()
    products = session.scalars(select(Product).where(Product.user_id == user_id)).all()
    existing = {
        e.product_id: e
        for e in session.scalars(select(ProductEmbedding).where(ProductEmbedding.user_id == user_id))
    }

    todo: list[tuple[Product, str, str]] = []
    for p in products:
        text = ProductRecord.from_row(p).searchable_text
        digest = _hash(text)
        emb = existing.get(p.product_id)
        if emb is not None and emb.text_hash == digest and emb.model == client.model_id:
            result.unchanged += 1
            # e.g. only the price changed (not part of the searchable text): mark as checked
            # so ensure_index() stops seeing this product as newer than its vector.
            if p.last_updated and emb.updated_at and p.last_updated > emb.updated_at:
                emb.updated_at = utcnow()
        else:
            todo.append((p, text, digest))

    if todo:
        vectors = client.embed([text for _, text, _ in todo])
        for (p, _text, digest), vec in zip(todo, vectors, strict=True):
            emb = existing.get(p.product_id)
            if emb is None:
                session.add(
                    ProductEmbedding(
                        product_id=p.product_id,
                        user_id=user_id,
                        model=client.model_id,
                        dim=len(vec),
                        vector=vec,
                        text_hash=digest,
                    )
                )
            else:
                emb.model, emb.dim, emb.vector, emb.text_hash = client.model_id, len(vec), vec, digest
        result.embedded = len(todo)
        session.flush()
        log.info("products embedded", extra={"embedded": result.embedded, "model": client.model_id})
    return result
