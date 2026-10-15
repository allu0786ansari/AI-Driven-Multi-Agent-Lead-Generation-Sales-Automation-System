"""Health, events API, logging, settings, migrations and the CLI scripts."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.config import BACKEND_DIR, Settings
from app.db.base import Base
from app.dev import payloads as p
from app.logging_config import JsonFormatter, PlainFormatter, mask_pii, trace_id_var
from app.worker.processor import process_pending
from tests.conftest import TEST_PASSWORD, run_migrations, signed_post


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["llm_provider"] == "fake"
    assert resp.headers["X-Request-ID"]


def test_health_deep_checks_llm(client: TestClient) -> None:
    assert client.get("/api/health", params={"deep": True}).json()["llm"] == "ok"


def test_request_id_propagated(client: TestClient) -> None:
    assert client.get("/api/health", headers={"X-Request-ID": "abc123"}).headers["X-Request-ID"] == "abc123"


def test_events_api(client: TestClient, auth_headers: dict[str, str]) -> None:
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text())
    signed_post(client, "/webhooks/meta/instagram", p.instagram_comment())
    process_pending()
    body = client.get("/api/events", headers=auth_headers).json()
    # WhatsApp messages are handled since Week 2, Instagram comments since Week 3.
    assert body["counts_by_status"] == {"processed": 2}
    assert {e["event_type"] for e in body["events"]} == {"whatsapp.message", "instagram.comment"}
    # Found in Week 4: result_note was documented in RUNLOG but not returned by the API.
    assert all(e["result_note"] for e in body["events"])
    filtered = client.get("/api/events", params={"status": "pending"}, headers=auth_headers).json()
    assert filtered["events"] == []


def test_events_api_requires_auth(client: TestClient) -> None:
    assert client.get("/api/events").status_code == 401


# ------------------------------------------------------------------- logging


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("call +91 98765 43210 now", "call ***10 now"),
        ("wa_id 919876543210", "wa_id ***10"),
        ("mail ravi.kumar@example.com", "mail r***@example.com"),
        ("order 123 qty 4", "order 123 qty 4"),
        # Found in the Week 1 sandbox run: these must NOT be masked.
        ("at 2026-10-14 14:04:19,814", "at 2026-10-14 14:04:19,814"),
        ("trace d00ee102934171c", "trace d00ee102934171c"),
        ("id 1234567890abcdef", "id 1234567890abcdef"),
        ("user 34932116-83e8-4d35-8164-ec09cfff5fa7", "user 34932116-83e8-4d35-8164-ec09cfff5fa7"),
        ("wamid.HBgM919876543210FQIAERgS", "wamid.HBgM919876543210FQIAERgS"),
    ],
)
def test_mask_pii(raw: str, masked: str) -> None:
    assert mask_pii(raw) == masked


def test_plain_log_keeps_timestamp_and_trace_masks_message() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(PlainFormatter("%(asctime)s [%(trace_id)s] %(message)s"))
    logger = logging.getLogger("test.plain")
    logger.addHandler(handler)
    logger.propagate = False
    token = trace_id_var.set("d00ee102934171c")
    try:
        logger.warning("lead %s", "+91 98765 43210")
    finally:
        trace_id_var.reset(token)
        logger.removeHandler(handler)
    line = stream.getvalue()
    assert "[d00ee102934171c] lead ***10" in line
    assert line[:4].isdigit()  # timestamp intact


def test_json_log_has_trace_id_and_masks_extra() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.json")
    logger.addHandler(handler)
    logger.propagate = False
    token = trace_id_var.set("trace-xyz")
    try:
        logger.warning("lead from %s", "919876543210", extra={"phone": "919876543210", "count": 3})
    finally:
        trace_id_var.reset(token)
        logger.removeHandler(handler)
    line = json.loads(stream.getvalue())
    assert line["trace_id"] == "trace-xyz"
    assert line["msg"] == "lead from ***10"
    assert line["phone"] == "***10"
    assert line["count"] == 3


# ------------------------------------------------------------------ settings


def test_relative_paths_resolved_against_backend_dir() -> None:
    s = Settings(database_url="sqlite:///./data/x.db", catalog_path="data/c.json")
    assert s.database_url == "sqlite:///" + (BACKEND_DIR / "data" / "x.db").resolve().as_posix()
    assert Path(s.catalog_path) == (BACKEND_DIR / "data" / "c.json").resolve()
    assert Settings(database_url="postgresql+psycopg://u:p@h/db").database_url.startswith("postgresql")


# ---------------------------------------------------------------- migrations


def test_migrations_match_models(tmp_path: Path) -> None:
    """Fails if a model changes without a new Alembic migration."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    run_migrations(url)
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_all_spec_tables_exist(db_url: str) -> None:
    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {"users", "leads", "products", "messages", "consent_events", "inbound_events"} <= tables


def test_every_business_table_has_user_id(db_url: str) -> None:
    engine = create_engine(db_url)
    insp = inspect(engine)
    for table in ("leads", "products", "messages", "consent_events", "inbound_events"):
        assert "user_id" in {c["name"] for c in insp.get_columns(table)}, table
    engine.dispose()


# -------------------------------------------------------------------- scripts


def test_create_user_script(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.create_user import main

    assert main(["--email", "a@b.com", "--password", TEST_PASSWORD, "--business-name", "Shop"]) == 0
    assert "Created user a@b.com" in capsys.readouterr().out
    assert main(["--email", "A@B.com", "--password", TEST_PASSWORD]) == 1
    assert main(["--email", "c@d.com", "--password", "short"]) == 1


def test_load_catalog_script(user, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    from scripts.load_catalog import main

    assert main(["--check-only"]) == 0
    assert main([]) == 0
    assert "8 created" in capsys.readouterr().out
    assert main([]) == 0
    assert "8 unchanged" in capsys.readouterr().out


def test_load_catalog_script_without_user(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.load_catalog import main

    assert main([]) == 1
    assert "No user found" in capsys.readouterr().err


def test_simulator_lists_scenarios(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.simulate import main

    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    for name in ("whatsapp", "ctwa", "leadgen", "ig-comment", "duplicate", "bad-signature"):
        assert name in out


def test_llm_smoke_script_with_fake(capsys: pytest.CaptureFixture[str]) -> None:
    from app.llm.factory import get_llm_client
    from scripts.llm_smoke import main

    get_llm_client.cache_clear()
    assert main() == 0
    assert "Structured" in capsys.readouterr().out
