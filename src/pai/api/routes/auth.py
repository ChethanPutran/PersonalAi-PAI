"""
Authentication routes: register, login, me.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid4

from pai.api.dependencies import get_current_user, get_db
from pai.security.auth import (
    create_access_token,
    hash_password,
    verify_password,
)
from pai.storage.models import UserModel

router = APIRouter(prefix="/auth", tags=["auth"])


# ============================================================
# Models
# ============================================================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    is_admin: bool = False


class MeResponse(BaseModel):
    id: str
    email: str
    is_admin: bool = False
    created_at: Optional[str] = None


# ============================================================
# Promote rules
# ============================================================

def _admin_emails() -> set[str]:
    raw = os.environ.get("PAI_ADMIN_EMAILS", "").strip()
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _should_be_admin(email: str, is_first_user: bool) -> bool:
    if is_first_user:
        return True
    return email.lower() in _admin_emails()


# ============================================================
# Register
# ============================================================

@router.post("/register", response_model=TokenResponse)
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    # 1. Existing user check
    existing = await session.execute(
        select(UserModel).where(UserModel.email == payload.email)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(409, "Email already registered")

    # 2. First-user check (drives admin promote)
    count_result = await session.execute(
        select(func.count()).select_from(UserModel)
    )
    is_first_user = (count_result.scalar_one() == 0)

    # 3. Create
    user = UserModel(
        id=str(uuid4()),
        email=payload.email,
        hashed_password=hash_password(payload.password),
        is_admin=_should_be_admin(payload.email, is_first_user),
    )
    session.add(user)
    await session.flush()

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        email=user.email,
        is_admin=user.is_admin,
    )


# ============================================================
# Login — self-heals admin flag
# ============================================================

@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await session.execute(
        select(UserModel).where(UserModel.email == payload.email)
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")

    # Promote if this email is in PAI_ADMIN_EMAILS but the row
    # was created before the flag was set.
    if not user.is_admin and payload.email.lower() in _admin_emails():
        user.is_admin = True
        await session.flush()

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        email=user.email,
        is_admin=user.is_admin,
    )


# ============================================================
# Me
# ============================================================

@router.get("/me", response_model=MeResponse)
async def me(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeResponse:
    result = await session.execute(
        select(UserModel).where(UserModel.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "User not found")

    return MeResponse(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        created_at=user.created_at.isoformat() if user.created_at else None,
    )