"""Week 6: container start-up (scripts/bootstrap.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Product, User
from app.db.session import session_scope
from scripts import bootstrap

GOOD_SECRET = "x" * 40


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    def set_(**values: str) -> None:
        for k, v in values.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()

    set_(JWT_SECRET=GOOD_SECRET)
    yield set_
    get_settings.cache_clear()


@pytest.mark.parametrize("secret", ["change-me-in-env", "short-secret", ""])
def test_refuses_weak_jwt_secret(env, secret: str, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    env(JWT_SECRET=secret)
    assert bootstrap.main([]) == 2
    assert "JWT_SECRET" in capsys.readouterr().out


def test_first_start_creates_account_and_loads_catalog_once(env, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    env(
        INITIAL_USER_EMAIL="owner@store.test",
        INITIAL_USER_PASSWORD="long-enough-pw",
        INITIAL_BUSINESS_NAME="Shop",
    )
    assert bootstrap.main([]) == 0
    out = capsys.readouterr().out
    assert "created the first account: owner@store.test" in out and "loaded the catalog" in out
    with session_scope() as s:
        [user] = s.scalars(select(User)).all()
        assert user.business_name == "Shop"
        products = s.scalar(select(func.count()).select_from(Product))
    assert products == 8

    assert bootstrap.main([]) == 0  # second start: nothing created again
    out = capsys.readouterr().out
    assert "created" not in out and "loaded the catalog" not in out
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(User)) == 1
        assert s.scalar(select(func.count()).select_from(Product)) == 8


def test_no_account_without_credentials(env, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    assert bootstrap.main([]) == 0
    assert "no account yet" in capsys.readouterr().out
    env(INITIAL_USER_EMAIL="owner@store.test", INITIAL_USER_PASSWORD="short")
    assert bootstrap.main([]) == 0
    assert "at least 10 characters" in capsys.readouterr().out
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(User)) == 0


def test_catalog_can_be_skipped(env) -> None:  # noqa: ANN001
    env(INITIAL_USER_EMAIL="o@x.test", INITIAL_USER_PASSWORD="long-enough-pw", LOAD_CATALOG_ON_START="false")
    bootstrap.main([])
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Product)) == 0


def test_warns_when_a_folder_is_not_writable(
    env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:  # noqa: ANN001
    """Linux host + Docker: ./exports is created owned by root, the server runs as user 10001."""
    locked = tmp_path / "exports"
    real_write = Path.write_text

    def write_text(self: Path, *a, **kw):  # noqa: ANN002, ANN003, ANN202
        if self.parent == locked:
            raise PermissionError(13, "Permission denied")
        return real_write(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", write_text)
    env(SHEETS_MODE="csv", SHEETS_CSV_PATH=str(locked / "leads_mirror.csv"))
    bootstrap.check_writable()
    out = capsys.readouterr().out
    assert f"cannot write to {locked}" in out and "sudo chown -R" in out
    assert "backups" not in out  # the backup folder is fine
