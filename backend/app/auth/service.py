"""User account operations."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import hash_password, verify_password
from app.db.models import User


class UserExistsError(ValueError):
    pass


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("timing-equalizer-not-a-real-password")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def create_user(
    session: Session,
    email: str,
    password: str,
    business_name: str = "",
    product_category: str = "",
) -> User:
    email = normalize_email(email)
    if session.scalar(select(User).where(User.email == email)):
        raise UserExistsError(f"A user with email {email} already exists")
    user = User(
        email=email,
        password_hash=hash_password(password),
        business_name=business_name,
        product_category=product_category,
    )
    session.add(user)
    session.flush()
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.is_active:
        # Still run a hash check so response time does not reveal whether the email exists.
        verify_password(password, _dummy_hash())
        return None
    return user if verify_password(password, user.password_hash) else None


def count_users(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User)) or 0
