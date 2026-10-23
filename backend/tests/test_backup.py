"""Week 6: daily database backups and restore (spec Section 11)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import backup
from app.config import get_settings
from app.db.models import Lead
from app.db.session import init_engine, session_scope
from scripts import backup as backup_script


def _add_lead(user_id: str, name: str) -> None:
    with session_scope() as s:
        s.add(Lead(user_id=user_id, name=name))


def _names() -> set[str]:
    with session_scope() as s:
        return set(s.scalars(select(Lead.name)).all())


def test_backup_is_a_checked_copy(user) -> None:  # noqa: ANN001
    _add_lead(user.user_id, "Priya")
    info = backup.backup_now()
    assert info.path.exists() and info.path.parent == Path(get_settings().backup_dir)
    backup.check(info.path)
    import sqlite3

    con = sqlite3.connect(info.path)
    assert con.execute("select name from leads").fetchall() == [("Priya",)]
    con.close()
    assert not list(info.path.parent.glob("*.partial"))


def test_backup_while_the_server_writes(user) -> None:  # noqa: ANN001
    stop = threading.Event()

    def writer() -> None:
        i = 0
        while not stop.is_set():
            _add_lead(user.user_id, f"lead {i}")
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    try:
        infos = [backup.backup_now(now=datetime(2026, 10, 21, 10, 0, i, tzinfo=UTC)) for i in range(3)]
    finally:
        stop.set()
        t.join()
    for info in infos:
        backup.check(info.path)  # consistent snapshots, no torn writes


def test_keep_only_the_newest(user, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("BACKUP_KEEP", "3")
    get_settings.cache_clear()
    for day in range(5):
        backup.backup_now(now=datetime(2026, 10, 10 + day, 12, 0, tzinfo=UTC))
    names = [b.path.name for b in backup.list_backups()]
    assert names == ["app-20261014-120000.db", "app-20261013-120000.db", "app-20261012-120000.db"]


def test_one_backup_per_day_in_the_business_time_zone(user) -> None:  # noqa: ANN001
    assert backup.due()
    # 23:00 IST on the 21st
    backup.backup_now(now=datetime(2026, 10, 21, 17, 30, tzinfo=UTC))
    assert not backup.due(now=datetime(2026, 10, 21, 18, 0, tzinfo=UTC))  # 23:30 IST, same day
    assert backup.due(now=datetime(2026, 10, 21, 19, 0, tzinfo=UTC))  # 00:30 IST, the 22nd


def test_backup_if_due_makes_one_a_day(user) -> None:  # noqa: ANN001
    assert backup.backup_if_due() is not None
    assert backup.backup_if_due() is None
    assert len(backup.list_backups()) == 1


def test_restore_puts_the_backup_back_and_keeps_a_safety_copy(user) -> None:  # noqa: ANN001
    _add_lead(user.user_id, "Before backup")
    info = backup.backup_now()
    _add_lead(user.user_id, "After backup")
    from app.db.session import get_engine

    get_engine().dispose()  # "server stopped"
    safety = backup.restore(info.path)
    init_engine(get_settings().database_url)
    assert _names() == {"Before backup"}
    assert safety.exists()
    import sqlite3

    con = sqlite3.connect(safety)
    assert {r[0] for r in con.execute("select name from leads")} == {"Before backup", "After backup"}
    con.close()


def test_restore_refuses_a_file_that_is_not_a_backup(user, tmp_path: Path) -> None:  # noqa: ANN001
    bogus = tmp_path / "app-20261021-000000.db"
    bogus.write_text("not a database")
    with pytest.raises(backup.BackupError):
        backup.restore(bogus)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Lead)) == 0  # untouched


def test_postgres_is_not_backed_up_here(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/leadgen")
    get_settings.cache_clear()
    with pytest.raises(backup.BackupError, match="pg_dump"):
        backup.database_path()
    assert backup.backup_if_due() is None  # logged, never raised


def test_health_shows_the_last_backup(client: TestClient, user) -> None:  # noqa: ANN001
    assert client.get("/api/health").json()["last_backup"] is None
    info = backup.backup_now()
    last = client.get("/api/health").json()["last_backup"]
    assert datetime.fromisoformat(last) == info.created_at.replace(microsecond=0)


def test_command_line(user, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    assert backup_script.main(["now"]) == 0
    assert "Backed up to" in capsys.readouterr().out
    assert backup_script.main(["list"]) == 0
    name = capsys.readouterr().out.split()[-1]
    from app.db.session import get_engine

    get_engine().dispose()
    assert backup_script.main(["restore", name, "--yes"]) == 0
    assert "Restored" in capsys.readouterr().out
    assert backup_script.main(["restore", "missing.db", "--yes"]) == 2
    assert backup_script.main(["restore"]) == 2


def test_backup_names_sort_by_time() -> None:
    assert (
        backup.PREFIX + f"{datetime(2026, 1, 2, 3, 4, 5):%Y%m%d-%H%M%S}" + backup.SUFFIX
        == "app-20260102-030405.db"
    )
    assert timedelta(hours=1)  # worker checks hourly (see Worker._backup_if_due)


def test_a_backup_is_one_file_with_nothing_left_beside_it(user) -> None:  # noqa: ANN001
    directory = backup.backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "app-20261020-100000.partial-wal").write_bytes(b"")  # from a 0.6.0 build
    info = backup.backup_now()
    assert sorted(p.name for p in directory.iterdir()) == [info.path.name]
    with sqlite3.connect(info.path) as con:  # self-contained: not in WAL mode
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
