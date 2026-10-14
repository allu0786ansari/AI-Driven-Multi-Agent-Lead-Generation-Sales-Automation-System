"""Measure product-search quality for an embedding model, per language.

    python -m scripts.eval_retrieval                                   # hashing (no model)
    python -m scripts.eval_retrieval --provider ollama --model bge-m3
    python -m scripts.eval_retrieval --provider ollama --model nomic-embed-text

Each line of the eval file is {"query": ..., "expected": [sku, ...], "lang": ...}.
A query counts as a hit if one of the expected SKUs is ranked in the top N.
The bundled file is a SYNTHETIC starter set - replace it with real customer
messages (spec Section 10) before making the final model choice.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from app.catalog.loader import CatalogError, load_catalog_file
from app.config import BACKEND_DIR, get_settings
from app.rag.embeddings import EmbeddingClient, EmbeddingError, HashingEmbeddings, OllamaEmbeddings
from app.rag.records import ProductRecord
from app.rag.retrieve import rank_products

DEFAULT_EVAL = BACKEND_DIR / "data" / "eval" / "retrieval_eval.sample.jsonl"


def _records(catalog_path: str) -> list[ProductRecord]:
    return [
        ProductRecord(
            product_id=p.sku,
            sku=p.sku,
            name=p.name,
            category=p.category,
            price=p.price,
            currency=p.currency,
            description=p.description,
            key_attributes=p.key_attributes,
            stock_status=p.stock_status,
        )
        for p in load_catalog_file(catalog_path)
    ]


def evaluate(client: EmbeddingClient, records: list[ProductRecord], cases: list[dict]) -> dict:
    vectors = client.embed([r.searchable_text for r in records])
    candidates = list(zip(records, vectors, strict=True))
    per_lang: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "hit1": 0, "hit3": 0, "mrr": 0.0})
    misses: list[str] = []
    for case in cases:
        ranked = [r.product.sku for r in rank_products(candidates, case["query"], client)]
        expected = set(case["expected"])
        rank = next((i + 1 for i, sku in enumerate(ranked) if sku in expected), None)
        stats = per_lang[case.get("lang", "?")]
        stats["n"] += 1
        stats["hit1"] += rank == 1
        stats["hit3"] += rank is not None and rank <= 3
        stats["mrr"] += 1 / rank if rank else 0
        if not rank or rank > 3:
            misses.append(
                f"  [{case.get('lang')}] {case['query']!r}: got {ranked[:3]}, expected {sorted(expected)}"
            )
    return {"per_lang": dict(per_lang), "misses": misses}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--provider", choices=["hashing", "ollama"], default="hashing")
    parser.add_argument("--model", default="bge-m3", help="Ollama embedding model (with --provider ollama)")
    parser.add_argument("--catalog", default=None, help="Catalog JSON (default: CATALOG_PATH)")
    parser.add_argument("--eval-file", default=str(DEFAULT_EVAL))
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        records = _records(args.catalog or settings.catalog_path)
    except CatalogError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    cases = [
        json.loads(line)
        for line in Path(args.eval_file).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    client: EmbeddingClient = (
        OllamaEmbeddings(settings.ollama_base_url, args.model, settings.ollama_timeout_seconds)
        if args.provider == "ollama"
        else HashingEmbeddings()
    )
    try:
        result = evaluate(client, records, cases)
    except EmbeddingError as exc:
        print(
            f"Embedding failed: {exc}\nIs Ollama running and the model pulled (ollama pull {args.model})?",
            file=sys.stderr,
        )
        return 1

    print(f"Model: {client.model_id}   Queries: {len(cases)}   Products: {len(records)}")
    print(f"{'language':<10}{'queries':>8}{'top-1':>8}{'top-3':>8}{'MRR':>7}")
    totals = {"n": 0, "hit1": 0, "hit3": 0, "mrr": 0.0}
    for lang, s in sorted(result["per_lang"].items()):
        n = s["n"]
        print(f"{lang:<10}{n:>8}{s['hit1'] / n:>8.0%}{s['hit3'] / n:>8.0%}{s['mrr'] / n:>7.2f}")
        for key in totals:
            totals[key] += s[key]
    n = totals["n"]
    print(f"{'ALL':<10}{n:>8}{totals['hit1'] / n:>8.0%}{totals['hit3'] / n:>8.0%}{totals['mrr'] / n:>7.2f}")
    if args.show_misses and result["misses"]:
        print("\nNot in top 3:")
        print("\n".join(result["misses"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
