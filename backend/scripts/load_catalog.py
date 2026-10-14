"""Validate a product catalog file and import it into the database.

    python -m scripts.load_catalog                       # uses CATALOG_PATH from .env
    python -m scripts.load_catalog --file data\\my_catalog.json
    python -m scripts.load_catalog --check-only          # validate, do not import

Importing the same file again updates changed products (matched by sku).
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.catalog.loader import CatalogError, import_catalog, load_catalog_file
from app.config import get_settings
from app.db.models import User
from app.db.session import session_scope
from app.db.tenancy import resolve_user_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", default=None, help="Catalog JSON file (default: CATALOG_PATH)")
    parser.add_argument("--user-email", default=None, help="Business to import for (default: the only user)")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)

    path = args.file or get_settings().catalog_path
    try:
        products = load_catalog_file(path)
    except CatalogError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{path}: {len(products)} products are valid.")
    if args.check_only:
        return 0

    with session_scope() as s:
        if args.user_email:
            user_id = s.scalar(select(User.user_id).where(User.email == args.user_email.strip().lower()))
        else:
            user_id = resolve_user_id(s)
        if user_id is None:
            print(
                "No user found. Create one first:  python -m scripts.create_user --email ...", file=sys.stderr
            )
            return 1
        result = import_catalog(s, user_id, products)
    print(f"Imported: {result.created} created, {result.updated} updated, {result.unchanged} unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
