"""
Password hashing and JWT helpers.

Token shape: HS256-signed JWT with `sub` = user_id.
No session state on the server — the token is the only credential.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JWT_SECRET = os.environ.get("PAI_JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_MINUTES = int(os.environ.get("PAI_JWT_EXPIRES_MINUTES", "10080"))  # 7 days

# bcrypt rejects inputs over 72 bytes; enforce here so callers get a
# clean error instead of a stack trace from deep inside the library.
_BCRYPT_MAX_BYTES = 72


# ---------------------------------------------------------------------------
# Password hashing — direct bcrypt, no passlib
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """
    Hash a password with bcrypt.

    Raises ValueError if the UTF-8 encoding exceeds 72 bytes.
    """
    if not isinstance(plain, str) or not plain:
        raise ValueError("password must be a non-empty string")

    pwd_bytes = plain.encode("utf-8")
    if len(pwd_bytes) > _BCRYPT_MAX_BYTES:
        raise ValueError(
            f"password too long: {len(pwd_bytes)} bytes "
            f"(limit is {_BCRYPT_MAX_BYTES})"
        )

    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """
    Return True if `plain` matches `hashed`.

    Never raises — any error means the password doesn't match.
    """
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8"),
            hashed.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRES_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """
    Return the user_id from a valid token, or None if invalid / expired.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        return None
    return user_id