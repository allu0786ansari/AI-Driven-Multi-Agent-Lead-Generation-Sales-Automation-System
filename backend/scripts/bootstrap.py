"""Container start-up (Docker): get everything ready, then run the server.

    python -m scripts.bootstrap          # prepare only
    python -m scripts.bootstrap serve    # prepare, then start the web server (the image's default)

Steps, all safe to repeat on every start:
1. Refuse to start with the default JWT_SECRET: the container is reachable from
   the network, and anyone could sign tokens with the published default.
2. Bring the database up to date (Alembic migrations).
3. If there is no account yet and INITIAL_USER_EMAIL / INITIAL_USER_PASSWORD
   are set, create it (business name from INITIAL_BUSINESS_NAME or BUSINESS_NAME).
4. If the business has no products and LOAD_CATALOG_ON_START is not "false",
   import the catalog at CATALOG_PATH.

One server process only: the background worker (webhooks, outbox, follow-ups,
backups) runs inside it, so do not start several replicas.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from app.config import BACKEND_DIR, get_settings

log = logging.getLogger("bootstrap")
WEAK_SECRETS = {"", "change-me-in-env", "changeme", "secret"}


def _say(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def check_secrets() -> bool:
    s = get_settings()
    if s.jwt_secret in WEAK_SECRETS or len(s.jwt_secret) < 32:
        _say(
            "JWT_SECRET is missing, the default or shorter than 32 characters. Set a long random value in "
            'backend/.env, e.g. the output of: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
        return False
    if s.meta_app_secret in WEAK_SECRETS:
        _say("warning: META_APP_SECRET is the default - webhooks from Meta will be rejected until you set it")
    return True


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    url = get_settings().database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    _say("database is up to date")


def first_account_and_catalog() -> None:
    from sqlalchemy import func, select

    from app.auth.service import create_user
    from app.catalog.loader import CatalogError, import_catalog, load_catalog_file
    from app.db.models import Product, User
    from app.db.session import init_engine, session_scope

    init_engine(get_settings().database_url)
    with session_scope() as s:
        users = s.scalars(select(User).order_by(User.created_at)).all()
        if not users:
            email = os.getenv("INITIAL_USER_EMAIL", "").strip()
            password = os.getenv("INITIAL_USER_PASSWORD", "")
            if not (email and password):
                _say(
                    "no account yet: set INITIAL_USER_EMAIL and INITIAL_USER_PASSWORD, or run "
                    "docker compose exec app python -m scripts.create_user --email you@example.com"
                )
                return
            if len(password) < 10:
                _say("INITIAL_USER_PASSWORD must be at least 10 characters; no account created")
                return
            name = os.getenv("INITIAL_BUSINESS_NAME") or get_settings().business_name
            users = [create_user(s, email, password, name, "")]
            _say(f"created the first account: {email}")
        user = users[0]
        if os.getenv("LOAD_CATALOG_ON_START", "true").strip().lower() in ("0", "false", "no"):
            return
        has_products = s.scalar(select(func.count()).where(Product.user_id == user.user_id))
        if has_products:
            return
        path = get_settings().catalog_path
        try:
            result = import_catalog(s, user.user_id, load_catalog_file(path))
        except (CatalogError, OSError) as exc:
            _say(
                f"catalog not loaded ({exc}); add products on the Products page or with scripts.load_catalog"
            )
            return
        _say(f"loaded the catalog from {path}: {result}")


def serve() -> None:
    port = os.getenv("PORT", "8000")
    args = [
        "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port,
        "--proxy-headers", "--forwarded-allow-ips", os.getenv("FORWARDED_ALLOW_IPS", "*"),
    ]  # fmt: skip
    _say(f"starting the server on port {port}")
    os.chdir(BACKEND_DIR)
    os.execvp(args[0], args)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not check_secrets():
        return 2
    migrate()
    first_account_and_catalog()
    if argv[:1] == ["serve"]:
        serve()  # does not return
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
