"""Create the business login (run once after `alembic upgrade head`).

    python -m scripts.create_user --email you@example.com --business-name "My Store"

You will be asked for the password (or pass --password for automation).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import inspect

from app.auth.security import PasswordPolicyError, validate_password
from app.auth.service import UserExistsError, create_user
from app.db.session import get_engine, session_scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--business-name", default="")
    parser.add_argument("--product-category", default="")
    parser.add_argument("--password", help="If omitted, you will be prompted (recommended)")
    args = parser.parse_args(argv)

    if not inspect(get_engine()).has_table("users"):
        print("Database tables not found. Run:  alembic upgrade head", file=sys.stderr)
        return 1

    password = args.password
    if password is None:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
    try:
        validate_password(password)
        with session_scope() as s:
            user = create_user(s, args.email, password, args.business_name, args.product_category)
            print(f"Created user {user.email} (user_id={user.user_id})")
    except (PasswordPolicyError, UserExistsError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
