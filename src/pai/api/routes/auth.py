"""
Authentication routes: register, login, me.

The UserModel field for the stored hash is `hashed_password`. If your
model uses `password_hash` instead, replace the three references in
this file (`user.hashed_password` reads, `hashed_password=` write)
before running.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4
from loguru import logger
from pai.api.dependencies import get_current_user, get_db
from pai.observability.tracer import get_tracer
from pai.security.auth import (
    ahash_password,
    averify_password,
    create_access_token,
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
# Admin-promotion rules
# ============================================================

def _admin_emails() -> set[str]:
    """
    Emails listed in PAI_ADMIN_EMAILS are promoted to admin on next login.

    Comma-separated, case-insensitive:
        PAI_ADMIN_EMAILS=alice@x.com,bob@y.com
    """
    raw = os.environ.get("PAI_ADMIN_EMAILS", "").strip()
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _should_be_admin(email: str, is_first_user: bool) -> bool:
    # The first user in the system is always admin — this is what
    # lets you bootstrap a fresh install.
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
    tracer = get_tracer()

    async with tracer.span(
        "auth.register",
        # Never log the full email — the domain is enough for
        # debugging without leaking PII to the trace store.
        email_domain=payload.email.split("@")[-1],
    ) as span:

        # 1. Existing-user check
        existing = await session.execute(
            select(UserModel).where(UserModel.email == payload.email)
        )
        if existing.scalar_one_or_none() is not None:
            span.set_attribute("outcome", "duplicate_email")
            raise HTTPException(409, "Email already registered")

        # 2. First-user check → drives admin auto-promotion
        count_result = await session.execute(
            select(func.count()).select_from(UserModel)
        )
        is_first_user = (count_result.scalar_one() == 0)
        span.set_attribute("is_first_user", is_first_user)

        # 3. Hash password (async — doesn't block the event loop)
        try:
            hashed = await ahash_password(payload.password)
        except ValueError as exc:
            span.set_attribute("outcome", "bad_password")
            span.set_error(str(exc))
            raise HTTPException(400, str(exc))

        # 4. Create user
        user = UserModel(
            id=str(uuid4()),
            email=payload.email,
            hashed_password=hashed,
            is_admin=_should_be_admin(payload.email, is_first_user),
        )
        session.add(user)
        await session.flush()

        span.set_attribute("user_id", user.id)
        span.set_attribute("is_admin", user.is_admin)
        span.set_attribute("outcome", "created")

        # 5. Issue token
        token = create_access_token(user.id)
        return TokenResponse(
            access_token=token,
            user_id=user.id,
            email=user.email,
            is_admin=user.is_admin,
        )


# ============================================================
# Login
# ============================================================

@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    tracer = get_tracer()

    logger.debug(f"Login attempt for email={payload.email}")

    async with tracer.span(
        "auth.login",
        email_domain=payload.email.split("@")[-1],
    ) as span:

        # 1. Look up user
        result = await session.execute(
            select(UserModel).where(UserModel.email == payload.email)
        )
        user = result.scalar_one_or_none()

        if user is None:
            logger.warning(f"Login failed for email={payload.email}: user not found")
            # Do not leak "email not registered" to the client —
            # same 401 as wrong-password — but DO record it in the
            # trace so operators can debug.
            span.set_attribute("outcome", "user_not_found")
            raise HTTPException(401, "Invalid email or password")

        span.set_attribute("user_id", user.id)

        # 2. Verify password (async, None-safe)
        matched = await averify_password(payload.password, user.hashed_password)
        if not matched:
            span.set_attribute("outcome", "wrong_password")
            # Also flag the specific case where the DB row has no hash,
            # which means the seed script wrote to the wrong column.
            if not user.hashed_password:
                span.set_attribute("hash_missing", True)
            raise HTTPException(401, "Invalid email or password")

        # 3. Self-heal admin flag if PAI_ADMIN_EMAILS was added after
        #    the row was created.
        if not user.is_admin and payload.email.lower() in _admin_emails():
            user.is_admin = True
            await session.flush()
            span.set_attribute("promoted_to_admin", True)

        span.set_attribute("is_admin", user.is_admin)
        span.set_attribute("outcome", "success")

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
    tracer = get_tracer()

    async with tracer.span("auth.me", user_id=user_id) as span:
        result = await session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            span.set_attribute("outcome", "not_found")
            raise HTTPException(404, "User not found")

        span.set_attribute("outcome", "success")
        return MeResponse(
            id=user.id,
            email=user.email,
            is_admin=user.is_admin,
            created_at=user.created_at.isoformat() if user.created_at else None,
        )