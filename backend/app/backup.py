"""Database backups (spec Section 11: daily backup, tested restore).

- One backup a day, made by the worker (checked every hour): SQLite's online
  backup API copies a consistent snapshot while the server keeps running.
- Every backup is checked (PRAGMA integrity_check) before it counts.
- The newest BACKUP_KEEP backups are kept; older ones are deleted.
- Restore: python -m scripts.backup restore <file> with the server stopped.
  The current database is kept next to it first ("...before-restore-<time>").

Postgres (later phase) is backed up with pg_dump instead; this module only
handles SQLite and says so.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db.base import utcnow

log = logging.getLogger(__name__)
PREFIX = "app-"
SUFFIX = ".db"


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    created_at: datetime  # UTC, from the file name
    size: int


def database_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise BackupError("built-in backups cover SQLite only; back up Postgres with pg_dump")
    path = url.removeprefix("sqlite:///")
    if path in ("", ":memory:"):
        raise BackupError("an in-memory database cannot be backed up")
    return Path(path)


def backup_dir() -> Path:
    return Path(get_settings().backup_dir)


def list_backups(directory: Path | None = None) -> list[BackupInfo]:
    """Newest first."""
    directory = directory or backup_dir()
    out = []
    for p in directory.glob(f"{PREFIX}*{SUFFIX}"):
        stamp = p.name.removeprefix(PREFIX).removesuffix(SUFFIX)
        try:
            created = datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=ZoneInfo("UTC"))
        except ValueError:
            continue
        out.append(BackupInfo(p, created, p.stat().st_size))
    return sorted(out, key=lambda b: b.created_at, reverse=True)


def check(path: Path) -> None:
    """Raise BackupError unless `path` is a readable, intact database with our tables."""
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise BackupError(f"{path.name} cannot be read: {exc}") from exc
    if result != "ok":
        raise BackupError(f"{path.name} failed the integrity check: {result}")
    if not {"leads", "messages", "alembic_version"} <= tables:
        raise BackupError(f"{path.name} does not look like this app's database")


def backup_now(directory: Path | None = None, now: datetime | None = None) -> BackupInfo:
    source = database_path()
    if not source.exists():
        raise BackupError(f"no database at {source}")
    directory = directory or backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    now = now or utcnow()
    target = directory / f"{PREFIX}{now:%Y%m%d-%H%M%S}{SUFFIX}"
    partial = target.with_suffix(".partial")
    src = sqlite3.connect(source)
    dst = sqlite3.connect(partial)
    try:
        with dst:
            src.backup(dst)  # consistent snapshot, even while the server writes
        # The copy inherits WAL mode; make it one self-contained file (reading a WAL
        # database leaves -wal/-shm files next to it, and a restore copies one file).
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        dst.close()
        src.close()
    try:
        check(partial)
    except BackupError:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(target)
    prune(directory)
    info = BackupInfo(target, now, target.stat().st_size)
    log.info("database backed up", extra={"file": target.name, "bytes": info.size})
    return info


def prune(directory: Path | None = None, keep: int | None = None) -> int:
    keep = keep if keep is not None else get_settings().backup_keep
    old = list_backups(directory)[keep:]
    for b in old:
        b.path.unlink(missing_ok=True)
    # Leftovers of an interrupted backup, or of 0.6.0 builds before this fix.
    for stray in (directory or backup_dir()).glob(f"{PREFIX}*.partial*"):
        stray.unlink(missing_ok=True)
    return len(old)


def due(now: datetime | None = None, directory: Path | None = None) -> bool:
    """True if there is no backup yet for today (business time zone)."""
    tz = ZoneInfo(get_settings().business_timezone)
    now = now or utcnow()
    latest = next(iter(list_backups(directory)), None)
    return latest is None or latest.created_at.astimezone(tz).date() < now.astimezone(tz).date()


def backup_if_due() -> BackupInfo | None:
    """The worker's daily backup. Errors are logged, never raised (the worker keeps going)."""
    if get_settings().backup_keep <= 0:
        return None
    try:
        database_path()
        return backup_now() if due() else None
    except BackupError as exc:
        log.warning("database backup skipped", extra={"reason": str(exc)})
    except (OSError, sqlite3.Error) as exc:
        log.error("database backup failed", extra={"error": str(exc)[:300]})
    return None


def restore(backup: Path, now: datetime | None = None) -> Path:
    """Replace the database with `backup` (server must be stopped). Returns the safety copy's path."""
    check(backup)
    target = database_path()
    now = now or utcnow()
    safety = target.with_name(f"{target.name}.before-restore-{now:%Y%m%d-%H%M%S}")
    if target.exists():
        shutil.copy2(target, safety)
    for extra in ("-wal", "-shm"):
        Path(f"{target}{extra}").unlink(missing_ok=True)
    tmp = target.with_suffix(".restoring")
    shutil.copy2(backup, tmp)
    tmp.replace(target)
    check(target)
    log.info("database restored", extra={"from": backup.name, "safety_copy": safety.name})
    return safety
