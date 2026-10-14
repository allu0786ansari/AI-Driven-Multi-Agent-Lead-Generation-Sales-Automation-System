"""Shared test fixtures.

Each test gets its own SQLite database, created by running the real Alembic
migrations (so the migrations themselves are tested on every run).
"""

from __future__ import annotations

import os

# Must be set before the app is imported.
os.environ.update(
    {
        "APP_ENV": "test",
        "LLM_PROVIDER": "fake",
        "WORKER_ENABLED": "false",
        "META_APP_SECRET": "test-app-secret",
        "META_VERIFY_TOKEN": "test-verify-token",
        "JWT_SECRET": "test-jwt-secret-that-is-long-enough-for-hs256",
        "LOG_JSON": "true",
        "WORKER_BACKOFF_BASE_SECONDS": "5",
        "WORKER_MAX_ATTEMPTS": "3",
    }
)

import json  # noqa: E402
import shutil  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.db.session import init_engine, session_scope  # noqa: E402
from app.router import dispatcher  # noqa: E402
from app.webhooks.signature import SIGNATURE_HEADER, compute_signature  # noqa: E402

TEST_PASSWORD = "correct-horse-battery"


def run_migrations(db_url: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = db_url
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def template_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("template") / "template.db"
    run_migrations(f"sqlite:///{path.as_posix()}")
    return path


@pytest.fixture(autouse=True)
def db_url(tmp_path: Path, template_db: Path) -> Iterator[str]:
    path = tmp_path / "test.db"
    shutil.copy(template_db, path)
    url = f"sqlite:///{path.as_posix()}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    init_engine(url)
    yield url
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def isolated_handlers() -> Iterator[None]:
    saved = dict(dispatcher._HANDLERS)
    yield
    dispatcher._HANDLERS.clear()
    dispatcher._HANDLERS.update(saved)


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def user():  # noqa: ANN201
    from app.auth.service import create_user

    with session_scope() as s:
        u = create_user(s, "Owner@Example.com", TEST_PASSWORD, "Test Store", "Kitchen appliances")
    return u


@pytest.fixture
def auth_headers(client: TestClient, user) -> dict[str, str]:  # noqa: ANN001
    resp = client.post("/api/auth/login", data={"username": user.email, "password": TEST_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def signed_post(
    client: TestClient, path: str, payload: dict[str, Any] | bytes, secret: str = "test-app-secret"
):  # noqa: ANN201
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return client.post(
        path,
        content=body,
        headers={"Content-Type": "application/json", SIGNATURE_HEADER: compute_signature(body, secret)},
    )
