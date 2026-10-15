"""Hybrid product retrieval: structured filters + vector similarity + keyword overlap.

The catalog lives in the main database (D-007). For a catalog of hundreds of
products, scoring every product in Python takes milliseconds; at larger scale
the same function moves to pgvector without changing its signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import StockStatus
from app.db.models import Product, ProductEmbedding
from app.rag.embeddings import EmbeddingClient, HashingEmbeddings, OllamaEmbeddings, cosine, tokenize
from app.rag.index import index_products
from app.rag.query import ProductFilters
from app.rag.records import ProductRecord

VECTOR_WEIGHT = 0.65
KEYWORD_WEIGHT = 0.35

# Words that say nothing about which product is meant (English + common Hinglish/Tanglish).
_STOPWORDS = {
    "a", "an", "the", "is", "are", "of", "for", "to", "in", "on", "and", "or", "with", "me", "my",
    "i", "you", "your", "it", "this", "that", "what", "which", "how", "much", "price", "cost",
    "please", "pls", "plz", "hi", "hello", "want", "need", "do", "does", "have", "has", "can",
    "available", "any", "details", "info", "information", "about", "tell", "send", "show", "give",
    "ka", "ki", "ke", "kya", "hai", "hain", "kitna", "kitne", "kitni", "mujhe", "chahiye", "bhai",
    "sir", "madam", "evlo", "enna", "venum", "irukka", "rate", "rs", "inr", "under", "below", "budget",
}  # fmt: skip


@dataclass(frozen=True)
class RetrievedProduct:
    product: ProductRecord
    score: float
    vector_score: float
    keyword_score: float


@lru_cache
def get_embedding_client() -> EmbeddingClient:
    s = get_settings()
    if s.embedding_provider == "ollama":
        return OllamaEmbeddings(s.ollama_base_url, s.embedding_model, s.ollama_timeout_seconds)
    return HashingEmbeddings()


def content_tokens(text: str) -> list[str]:
    """Words that identify a product (stopwords removed)."""
    return [t for t in tokenize(text) if t not in _STOPWORDS and (len(t) >= 2 or t.isdigit())]


def mentions_product(text: str, product: ProductRecord) -> bool:
    """True if a word of the message names the product or its category ("fryer", "kettle", "mixer").

    Description words don't count, so "delivery this week?" does not "name" a kettle whose
    description happens to share a few letters.
    """
    names = set(tokenize(f"{product.name} {product.category}"))
    words = [t for t in content_tokens(text) if len(t) >= 3]
    return any(t in names or (len(t) >= 4 and any(n.startswith(t[:4]) for n in names)) for t in words)


def keyword_score(query_tokens: list[str], product: ProductRecord) -> float:
    """Share of the query's meaningful words found in the product (prefix match for plurals)."""
    if not query_tokens:
        return 0.0
    product_tokens = set(tokenize(product.searchable_text))
    hits = 0.0
    for q in query_tokens:
        if q in product_tokens:
            hits += 1
        elif len(q) >= 4 and any(t.startswith(q[:4]) and abs(len(t) - len(q)) <= 2 for t in product_tokens):
            hits += 0.7
    return hits / len(query_tokens)


def _passes(p: ProductRecord, f: ProductFilters | None) -> bool:
    if f is None:
        return True
    if f.category and f.category.casefold() not in p.category.casefold():
        return False
    if f.max_price is not None and p.price > f.max_price:
        return False
    if f.min_price is not None and p.price < f.min_price:
        return False
    return not (f.in_stock_only and p.stock_status == StockStatus.OUT_OF_STOCK)


def ensure_index(session: Session, user_id: str, client: EmbeddingClient) -> None:
    """Embed products that are new, changed, or embedded with another model.

    Cheap check first (counts and latest timestamps); only if something looks
    out of date does index_products compare text hashes product by product.
    """
    total, latest_product = session.execute(
        select(func.count(), func.max(Product.last_updated)).where(Product.user_id == user_id)
    ).one()
    current, latest_embedding = session.execute(
        select(func.count(), func.max(ProductEmbedding.updated_at)).where(
            ProductEmbedding.user_id == user_id, ProductEmbedding.model == client.model_id
        )
    ).one()
    stale = latest_product is not None and (latest_embedding is None or latest_product > latest_embedding)
    if current != total or stale:
        index_products(session, user_id, client)


def rank_products(
    candidates: list[tuple[ProductRecord, list[float]]],
    query: str,
    client: EmbeddingClient,
    filters: ProductFilters | None = None,
) -> list[RetrievedProduct]:
    """Score products against the query, best first. Pure function (used by the eval script too)."""
    if not candidates:
        return []
    query_vec = client.embed([query])[0]
    query_tokens = content_tokens(query)
    scored: list[RetrievedProduct] = []
    for record, vector in candidates:
        if not _passes(record, filters):
            continue
        v = max(cosine(query_vec, vector), 0.0)
        k = keyword_score(query_tokens, record)
        scored.append(RetrievedProduct(record, VECTOR_WEIGHT * v + KEYWORD_WEIGHT * k, v, k))
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored


def get_product_context(
    session: Session,
    user_id: str,
    query: str,
    top_k: int | None = None,
    filters: ProductFilters | None = None,
    client: EmbeddingClient | None = None,
    min_score: float | None = None,
) -> list[RetrievedProduct]:
    """Spec 3.3: rag_pipeline.get_product_context(query, top_k, filters)."""
    settings = get_settings()
    client = client or get_embedding_client()
    top_k = top_k or settings.rag_top_k
    min_score = settings.rag_min_score if min_score is None else min_score

    ensure_index(session, user_id, client)
    rows = session.execute(
        select(Product, ProductEmbedding.vector)
        .join(ProductEmbedding, ProductEmbedding.product_id == Product.product_id)
        .where(Product.user_id == user_id, ProductEmbedding.model == client.model_id)
    ).all()
    candidates = [(ProductRecord.from_row(p), vector) for p, vector in rows]
    scored = rank_products(candidates, query, client, filters)
    return [r for r in scored if r.score >= min_score][:top_k]
