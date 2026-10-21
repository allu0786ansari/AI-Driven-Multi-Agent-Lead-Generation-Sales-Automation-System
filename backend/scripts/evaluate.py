"""Evaluation harness (proposal Section 9, spec Section 10).

Replays labelled customer messages through the real agents and checks the
results against the prototype's targets:

    python -m scripts.evaluate                       # the bundled sample set, the model in backend/.env
    python -m scripts.evaluate --file data/eval/my_messages.jsonl --catalog data/my_catalog.json

Nothing is sent and your database is not touched: a temporary database is
built from the catalog file. Uses the LLM and embedding settings from
backend/.env (LLM_PROVIDER=fake means keyword rules only, no model).

Each line of the file is one case (see data/eval/README.md):
  {"id": "c01", "kind": "comment", "text": "Price?", "intent": "product_question", "lang": "en"}
  {"id": "m01", "kind": "message", "text": "6L air fryer price?", "expect": {"skus": ["AF-6L-WIN"]}}
  expect keys: skus (any of them in the reply), handoff (true: a person should answer),
  opt_out (true/false), slots ({budget, quantity, timeline, city}), context_interest (earlier product)

Targets (proposal Section 9; the third, salesperson agreement with "hot", is
measured on the dashboard's Analytics page):
  comment action correct  >= 90%   (reply / complaint / hide spam / ignore)
  product answer correct  >= 95%   (right product, or correctly handed to a person)
  replies with a wrong price or invented offer: 0
  opt-out words: all found, no false opt-outs

Writes data/eval/reports/eval_<time>.md and .json. Exit code 1 if a target is missed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import BACKEND_DIR, get_settings

DEFAULT_FILE = BACKEND_DIR / "data" / "eval" / "agents_eval.sample.jsonl"
REPORT_DIR = BACKEND_DIR / "data" / "eval" / "reports"
ACTION = {
    "purchase_interest": "reply",
    "product_question": "reply",
    "complaint": "complaint",
    "spam": "hide",
    "other": "ignore",
}
TARGETS = {"comment_action": 90.0, "product_answer": 95.0}


@dataclass
class Outcome:
    case: dict[str, Any]
    ok: bool
    got: str
    checks: dict[str, bool] = field(default_factory=dict)
    detail: str = ""


def _prepare_database(catalog: Path) -> str:
    """Temporary database with the real migrations, one business and the catalog."""
    from alembic import command
    from alembic.config import Config

    from app.auth.service import create_user
    from app.catalog.loader import import_catalog, load_catalog_file
    from app.db.session import init_engine, session_scope

    path = Path(tempfile.mkdtemp(prefix="leadgen-eval-")) / "eval.db"
    url = f"sqlite:///{path.as_posix()}"
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    init_engine(url)
    with session_scope() as s:
        user = create_user(s, "eval@example.com", "evaluation-only-password", "Evaluation", "")
        import_catalog(s, user.user_id, load_catalog_file(str(catalog)))
        return user.user_id


def _comment(case: dict[str, Any]) -> Outcome:
    from app.agents.comments import classify_comment

    intent, how = classify_comment(case["text"])
    expected = case["intent"]
    action_ok = ACTION[str(intent)] == ACTION[expected]
    return Outcome(
        case, action_ok, f"{intent} ({how})",
        {"comment_action": action_ok, "comment_intent": str(intent) == expected},
    )  # fmt: skip


def _message(case: dict[str, Any], user_id: str) -> Outcome:
    from sqlalchemy.orm import Session

    from app.agents.consent import is_opt_out
    from app.agents.guardrails import validate_reply
    from app.agents.responder import generate_product_reply, retrieve_for_message
    from app.db.models import Lead
    from app.db.session import get_engine
    from app.qualify.agent import preview_next_question, process_message
    from app.qualify.slots import needs_support, wants_human

    expect = case.get("expect", {})
    text = case["text"]
    checks: dict[str, bool] = {}
    details: list[str] = []

    opted_out = is_opt_out(text)
    if "opt_out" in expect or opted_out:
        checks["opt_out"] = opted_out == bool(expect.get("opt_out"))
        details.append(f"opt-out {'yes' if opted_out else 'no'}")
    if opted_out:  # the agent stops here: confirmation only
        return Outcome(case, all(checks.values()), "opt-out", checks, "; ".join(details))

    with Session(get_engine()) as s:
        lead = Lead(user_id=user_id, product_interest=case.get("context_interest"), qualify_slots={})
        s.add(lead)
        s.flush()
        human = wants_human(text) or needs_support(text)  # same order as app/agents/engage.py
        if human:
            got = "asks for a person / complaint -> handoff"
            handed_off, skus, wrong_price = True, [], False
        else:
            retrieved = retrieve_for_message(s, user_id, text, lead.product_interest)
            question = preview_next_question(s, lead, text, retrieved[0])
            outcome = generate_product_reply(
                s, lead, text, channel_label="WhatsApp", next_question=question, retrieved=retrieved
            )
            skus = [p.sku for p in outcome.products]
            handed_off = outcome.needs_human or outcome.source == "holding"
            check = validate_reply(outcome.text, outcome.products, text)
            wrong_price = not check.ok
            got = f"{outcome.source}: {', '.join(skus) or 'no product'}" + (
                " -> handoff" if handed_off else ""
            )
            if wrong_price:
                details.append("guardrail: " + "; ".join(check.problems))
            q = process_message(s, lead, text, outcome.extracted, outcome.hits)
            if "slots" in expect:
                found = q.slots.model_dump(mode="json")
                for key, want in expect["slots"].items():
                    have = found.get(key)
                    same = have is not None and (
                        Decimal(str(have)) == Decimal(str(want)) if key == "budget" else have == want
                    )
                    checks[f"slot_{key}"] = same
                    if not same:
                        details.append(f"{key}: expected {want}, got {have}")
        s.rollback()

    if "skus" in expect or expect.get("handoff"):
        if expect.get("handoff"):
            answer_ok = handed_off
        else:
            answer_ok = bool(set(expect["skus"]) & set(skus)) and not (handed_off and not skus)
        checks["product_answer"] = answer_ok and not wrong_price
        checks["no_wrong_price"] = not wrong_price
    return Outcome(case, all(checks.values()), got, checks, "; ".join(details))


def _rate(outcomes: list[Outcome], key: str) -> tuple[int, int]:
    relevant = [o for o in outcomes if key in o.checks]
    return sum(o.checks[key] for o in relevant), len(relevant)


def _pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def evaluate(cases: list[dict[str, Any]], catalog: Path) -> dict[str, Any]:
    user_id = _prepare_database(catalog)
    from app.llm.factory import get_llm_client

    started = time.monotonic()
    outcomes = [_comment(c) if c["kind"] == "comment" else _message(c, user_id) for c in cases]
    settings = get_settings()
    llm = get_llm_client()
    metrics: dict[str, Any] = {}
    for key in ("comment_action", "comment_intent", "product_answer", "no_wrong_price", "opt_out"):
        n, d = _rate(outcomes, key)
        metrics[key] = {"correct": n, "total": d, "pct": _pct(n, d)}
    slot_keys = sorted({k for o in outcomes for k in o.checks if k.startswith("slot_")})
    metrics["slots"] = {
        k[5:]: dict(zip(("correct", "total"), _rate(outcomes, k), strict=True)) for k in slot_keys
    }

    by_lang: dict[str, list[bool]] = defaultdict(list)
    for o in outcomes:
        if "product_answer" in o.checks or "comment_action" in o.checks:
            by_lang[o.case.get("lang", "?")].append(
                o.checks.get("product_answer", o.checks.get("comment_action"))
            )
    targets = {
        "comment_action": (metrics["comment_action"]["pct"] or 0) >= TARGETS["comment_action"],
        "product_answer": (metrics["product_answer"]["pct"] or 0) >= TARGETS["product_answer"],
        "no_wrong_price": metrics["no_wrong_price"]["correct"] == metrics["no_wrong_price"]["total"],
        "opt_out": metrics["opt_out"]["correct"] == metrics["opt_out"]["total"],
    }
    rules_only = " (keyword rules, no model)"
    model = settings.llm_provider + (
        f" ({settings.ollama_model})" if settings.llm_provider == "ollama" else rules_only
    )
    return {
        "when": datetime.now().isoformat(timespec="seconds"),
        "llm": model,
        "llm_reachable": bool(llm.health()) if settings.llm_provider == "ollama" else None,
        "embeddings": f"{settings.embedding_provider}"
        + (f" ({settings.embedding_model})" if settings.embedding_provider == "ollama" else ""),
        "cases": len(cases),
        "seconds": round(time.monotonic() - started, 1),
        "metrics": metrics,
        "by_language": {k: {"correct": sum(v), "total": len(v)} for k, v in sorted(by_lang.items())},
        "targets_met": targets,
        "failures": [
            {"id": o.case["id"], "lang": o.case.get("lang"), "text": o.case["text"],
             "expected": o.case.get("intent") or o.case.get("expect"), "got": o.got,
             "failed": [k for k, v in o.checks.items() if not v], "detail": o.detail}
            for o in outcomes if not o.ok
        ],
    }  # fmt: skip


def _line(name: str, m: dict[str, Any], target: str = "") -> str:
    pct = "-" if m["pct"] is None else f"{m['pct']:.1f}%"
    return f"| {name} | {m['correct']} / {m['total']} | {pct} | {target} |"


def markdown(r: dict[str, Any]) -> str:
    m, t = r["metrics"], r["targets_met"]

    def mark(ok: bool) -> str:
        return "met" if ok else "**missed**"

    lines = [
        f"# Evaluation report - {r['when']}",
        "",
        f"Model: {r['llm']}. Search: {r['embeddings']}. {r['cases']} cases, {r['seconds']} s.",
        "",
        "| Check | Correct | Rate | Target |",
        "|---|---|---|---|",
        _line("Comment: right action (reply / complaint / hide / ignore)", m["comment_action"],
              f">= 90% ({mark(t['comment_action'])})"),
        _line("Comment: exact intent", m["comment_intent"], "info"),
        _line("Message: right product, or handed to a person", m["product_answer"],
              f">= 95% ({mark(t['product_answer'])})"),
        _line("Replies without a wrong price or invented offer", m["no_wrong_price"],
              f"all ({mark(t['no_wrong_price'])})"),
        _line("Opt-out words recognised, no false opt-outs", m["opt_out"], f"all ({mark(t['opt_out'])})"),
        "",
        "Details the customer gave (budget, quantity, timeline, city):",
        "",
        "| Detail | Correct |",
        "|---|---|",
        *[f"| {k} | {v['correct']} / {v['total']} |" for k, v in m["slots"].items()],
        "",
        "By language (comment actions and product answers):",
        "",
        "| Language | Correct |",
        "|---|---|",
        *[f"| {k} | {v['correct']} / {v['total']} |" for k, v in r["by_language"].items()],
        "",
        f"## Failures ({len(r['failures'])})",
        "",
    ]  # fmt: skip
    if not r["failures"]:
        lines.append("None.")
    for f in r["failures"]:
        expected = json.dumps(f["expected"], ensure_ascii=False)
        lines.append(
            f'- `{f["id"]}` ({f["lang"]}) "{f["text"]}" - expected {expected}, got {f["got"]}'
            + (f"; {f['detail']}" if f["detail"] else "")
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--catalog", type=Path, default=Path(get_settings().catalog_path))
    parser.add_argument("--no-report", action="store_true", help="print only, write no report files")
    args = parser.parse_args(argv)
    try:
        cases = [
            json.loads(line) for line in args.file.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read {args.file}: {exc}", file=sys.stderr)
        return 2
    if os.getenv("PYTHONIOENCODING") is None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console: Hindi / Tamil text

    import logging

    logging.disable(logging.WARNING)  # the per-case warnings are in the report
    try:
        report = evaluate(cases, args.catalog)
    finally:
        logging.disable(logging.NOTSET)
    text = markdown(report)
    print(text)
    if not args.no_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        (REPORT_DIR / f"eval_{stamp}.md").write_text(text, encoding="utf-8")
        (REPORT_DIR / f"eval_{stamp}.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        print(f"Report: {REPORT_DIR / f'eval_{stamp}.md'}")
    return 0 if all(report["targets_met"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
