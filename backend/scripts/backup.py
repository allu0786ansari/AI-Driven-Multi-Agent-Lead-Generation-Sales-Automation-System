"""Database backups from the command line (the server also makes one every day).

    python -m scripts.backup                 # back up now
    python -m scripts.backup list            # the backups there are, newest first
    python -m scripts.backup restore FILE    # put a backup back (stop the server first!)

Backups live in BACKUP_DIR (default backend/data/backups; /data/backups in Docker).
Restore keeps the current database next to it as "app.db.before-restore-<time>".
In Docker:  docker compose stop app
            docker compose run --rm app python -m scripts.backup restore /data/backups/<file>
            docker compose start app
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.backup import BackupError, backup_dir, backup_now, list_backups, restore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", nargs="?", choices=["now", "list", "restore"], default="now")
    parser.add_argument("file", nargs="?", help="backup file to restore (name or path)")
    parser.add_argument("--yes", action="store_true", help="restore without asking (server already stopped)")
    args = parser.parse_args(argv)
    try:
        if args.command == "now":
            info = backup_now()
            print(f"Backed up to {info.path} ({info.size // 1024} KB)")
        elif args.command == "list":
            backups = list_backups()
            if not backups:
                print(f"No backups in {backup_dir()} yet.")
            for b in backups:
                print(f"{b.created_at:%Y-%m-%d %H:%M} UTC  {b.size // 1024:>7} KB  {b.path.name}")
        else:
            if not args.file:
                print("Which backup? python -m scripts.backup list", file=sys.stderr)
                return 2
            path = Path(args.file)
            if not path.exists():
                path = backup_dir() / args.file
            if not path.exists():
                print(f"No backup {args.file}", file=sys.stderr)
                return 2
            if not args.yes:
                answer = input("Stop the server first. Replace the database with this backup? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("Nothing changed.")
                    return 1
            safety = restore(path)
            print(f"Restored {path.name}. The previous database is kept as {safety}.")
    except BackupError as exc:
        print(f"Backup error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
