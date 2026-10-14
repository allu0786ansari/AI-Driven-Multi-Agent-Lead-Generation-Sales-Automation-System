"""Login and current-user endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.auth.security import create_access_token
from app.auth.service import authenticate
from app.config import get_settings
from app.db.session import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    user_id: str
    email: str
    business_name: str
    product_category: str


@router.post("/login", response_model=TokenResponse)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Standard OAuth2 password form: fields `username` (the email) and `password`."""
    user = authenticate(db, form.username, form.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    minutes = get_settings().jwt_expire_minutes
    return TokenResponse(access_token=create_access_token(user.user_id), expires_in=minutes * 60)


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser) -> MeResponse:
    return MeResponse(
        user_id=user.user_id,
        email=user.email,
        business_name=user.business_name,
        product_category=user.product_category,
    )
