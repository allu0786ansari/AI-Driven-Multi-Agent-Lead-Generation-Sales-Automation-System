"""RAG: embeddings, indexing with change detection, hybrid retrieval, budget parsing, eval script."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import Product, ProductEmbedding
from app.db.session import session_scope
from app.rag.embeddings import EmbeddingError, HashingEmbeddings, OllamaEmbeddings, cosine
from app.rag.index import index_products
from app.rag.query import ProductFilters, parse_price_constraints
from app.rag.records import format_price
from app.rag.retrieve import get_product_context


def test_hashing_embeddings_deterministic_and_normalized() -> None:
    emb = HashingEmbeddings()
    a1, a2, b = emb.embed(["air fryer 6 litre", "air fryer 6 litre", "water purifier"])
    assert a1 == a2
    assert abs(sum(v * v for v in a1) - 1.0) < 1e-9
    assert cosine(a1, emb.embed(["air fryers"])[0]) > cosine(a1, b)


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        ("1199", "₹1,199"),
        ("112999", "₹1,12,999"),
        ("999.5", "₹999.50"),
        ("12999.00", "₹12,999"),
        ("75", "₹75"),
    ],
)
def test_format_price_indian_grouping(amount: str, text: str) -> None:
    assert format_price(Decimal(amount)) == text


def _count_embeddings() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(ProductEmbedding))


def test_index_only_changed_products(catalog) -> None:  # noqa: ANN001
    emb = HashingEmbeddings()
    with session_scope() as s:
        assert index_products(s, catalog.user_id, emb).embedded == 8
    with session_scope() as s:
        r = index_products(s, catalog.user_id, emb)
        assert (r.embedded, r.unchanged) == (0, 8)
    with session_scope() as s:
        s.scalar(select(Product).where(Product.sku == "EK-1.8-SS")).description = "Now with a glass body."
    with session_scope() as s:
        r = index_products(s, catalog.user_id, emb)
        assert (r.embedded, r.unchanged) == (1, 7)
    # A model switch re-embeds everything.
    with session_scope() as s:
        assert index_products(s, catalog.user_id, HashingEmbeddings(dim=256)).embedded == 8
    assert _count_embeddings() == 8


def test_retrieval_builds_index_automatically_and_ranks(catalog) -> None:  # noqa: ANN001
    with session_scope() as s:
        hits = get_product_context(s, catalog.user_id, "air fryer ka price kya hai")
    assert {h.product.sku for h in hits[:2]} == {"AF-4L-DIG", "AF-6L-WIN"}
    assert _count_embeddings() == 8
    assert all(0 <= h.score <= 1 for h in hits)


def test_price_only_change_does_not_keep_reindexing(catalog) -> None:  # noqa: ANN001
    with session_scope() as s:
        get_product_context(s, catalog.user_id, "kettle")
    with session_scope() as s:
        s.scalar(select(Product).where(Product.sku == "EK-1.8-SS")).price = Decimal("1099")
    with session_scope() as s:
        get_product_context(s, catalog.user_id, "kettle")
    with session_scope() as s:
        newest_product = s.scalar(select(func.max(Product.last_updated)))
        newest_vector = s.scalar(select(func.max(ProductEmbedding.updated_at)))
    assert newest_vector >= newest_product


def test_filters(catalog) -> None:  # noqa: ANN001
    with session_scope() as s:
        cheap = get_product_context(
            s,
            catalog.user_id,
            "kitchen appliance",
            top_k=8,
            filters=ProductFilters(max_price=Decimal("2000")),
            min_score=0,
        )
        assert cheap and all(h.product.price <= 2000 for h in cheap)
        in_stock = get_product_context(
            s,
            catalog.user_id,
            "hand blender",
            top_k=8,
            filters=ProductFilters(in_stock_only=True),
            min_score=0,
        )
        assert "HB-400-SET" not in {h.product.sku for h in in_stock}
        cat = get_product_context(
            s, catalog.user_id, "anything", top_k=8, filters=ProductFilters(category="air"), min_score=0
        )
        assert {h.product.category for h in cat} == {"Air Fryers"}


def test_min_score_drops_irrelevant(catalog) -> None:  # noqa: ANN001
    with session_scope() as s:
        assert get_product_context(s, catalog.user_id, "air fryer", min_score=0.99) == []


def test_no_products_returns_empty(user) -> None:  # noqa: ANN001
    with session_scope() as s:
        assert get_product_context(s, user.user_id, "air fryer") == []


@pytest.mark.parametrize(
    ("text", "max_price", "min_price"),
    [
        ("air fryer under 5000", 5000, None),
        ("budget is Rs 3,500", 3500, None),
        ("5k tak ka mixer", 5000, None),
        ("6000 se kam wala", 6000, None),
        ("below ₹2,000 please", 2000, None),
        ("something above 10000", None, 10000),
        ("2 years warranty under 2 years", None, None),
        ("air fryer price", None, None),
    ],
)
def test_parse_price_constraints(text: str, max_price, min_price) -> None:  # noqa: ANN001
    f = parse_price_constraints(text)
    assert f.max_price == (Decimal(max_price) if max_price else None)
    assert f.min_price == (Decimal(min_price) if min_price else None)


def test_ollama_embeddings_request_and_errors() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0]] * len(seen["input"])})

    client = OllamaEmbeddings("http://o.test", "bge-m3", transport=httpx.MockTransport(handler))
    assert client.embed(["a", "b"]) == [[0.6, 0.8], [0.6, 0.8]]
    assert seen == {"model": "bge-m3", "input": ["a", "b"]}
    assert client.model_id == "ollama:bge-m3"

    bad = OllamaEmbeddings(
        "http://o.test", "x", transport=httpx.MockTransport(lambda r: httpx.Response(404, text="nope"))
    )
    with pytest.raises(EmbeddingError, match="404"):
        bad.embed(["a"])


def test_eval_script_hashing(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.eval_retrieval import main

    assert main(["--show-misses"]) == 0
    out = capsys.readouterr().out
    assert "hashing-v1-512" in out
    en_line = next(line for line in out.splitlines() if line.startswith("en "))
    assert en_line.split()[3] == "100%"  # top-3 on English queries


def test_load_catalog_script_indexes(user, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    from scripts.load_catalog import main

    assert main([]) == 0
    assert "Search index (hashing-v1-512): 8 embedded" in capsys.readouterr().out
