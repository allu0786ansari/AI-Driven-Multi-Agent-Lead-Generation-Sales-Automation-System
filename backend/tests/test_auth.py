from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.security import (
    PasswordPolicyError,
    create_access_token,
    hash_password,
    verify_password,
)
from app.auth.service import UserExistsError, create_user
from app.db.session import session_scope
from tests.conftest import TEST_PASSWORD


def test_password_hash_roundtrip() -> None:
    h = hash_password("a-good-password")
    assert verify_password("a-good-password", h)
    assert not verify_password("wrong-password", h)
    assert not verify_password("x", "not-a-bcrypt-hash")


@pytest.mark.parametrize("bad", ["short", "x" * 73])
def test_password_policy(bad: str) -> None:
    with pytest.raises(PasswordPolicyError):
        hash_password(bad)


def test_duplicate_email_rejected_case_insensitive(user) -> None:  # noqa: ANN001
    with pytest.raises(UserExistsError), session_scope() as s:
        create_user(s, "owner@example.COM", "another-password")


def test_login_and_me(client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = client.get("/api/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "owner@example.com"
    assert body["business_name"] == "Test Store"


def test_login_wrong_password(client: TestClient, user) -> None:  # noqa: ANN001
    resp = client.post("/api/auth/login", data={"username": user.email, "password": "wrong-password"})
    assert resp.status_code == 401


def test_login_unknown_email(client: TestClient) -> None:
    resp = client.post("/api/auth/login", data={"username": "nobody@example.com", "password": TEST_PASSWORD})
    assert resp.status_code == 401


def test_me_requires_token(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_expired_token_rejected(client: TestClient, user) -> None:  # noqa: ANN001
    token = create_access_token(user.user_id, expires_minutes=-1)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_for_deleted_user_rejected(client: TestClient) -> None:
    token = create_access_token("00000000-0000-0000-0000-000000000000")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
