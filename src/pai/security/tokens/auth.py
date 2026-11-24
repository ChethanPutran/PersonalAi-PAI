"""
Password hashing and JWT helpers.

Design notes:

- bcrypt is used directly (passlib is unmaintained and incompatible
  with bcrypt >= 4.1 — see the `__about__` version-detection breakage).
- `verify_password` is None-safe: a missing/empty/malformed hash
  returns False instead of raising. This lets `login` return a clean
  401 without needing a try/except around the verify call.
- bcrypt is CPU-bound (~250ms per call at rounds=12). Both `hash_password`
  and `verify_password` are sync; async wrappers `ahash_password` and
  `averify_password` exist to run them in a thread so they don't block
  the event loop. Routes should use the async variants.
- Token shape: HS256 JWT with `sub` = user_id. No server-side sessions.

Tracing:

- Each operation produces a span with metadata, never the password or
  hash itself. This lets you see *why* an auth action failed (missing
  field vs. wrong password vs. expired token) without ever writing
  credentials to the trace store.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt
from loguru import logger

from pai.observability.tracer import get_tracer


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JWT_SECRET = os.environ.get("PAI_JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_MINUTES = int(
    os.environ.get("PAI_JWT_EXPIRES_MINUTES", "10080")     # 7 days
)

# bcrypt rejects inputs over 72 bytes. Truncate here so a user with a
# 100-char password doesn't get an opaque ValueError at login time.
# Consistent truncation on both hash and verify paths makes this safe.
_BCRYPT_MAX_BYTES = 72

# Warn once at import time if the JWT secret is still the default.
if JWT_SECRET == "change-me-in-production":
    logger.warning(
        "PAI_JWT_SECRET is the default value. "
        "Set it in .env before deploying."
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _truncate(password: str) -> bytes:
    """UTF-8 encode and truncate to bcrypt's 72-byte limit."""
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


# ---------------------------------------------------------------------------
# Password hashing (sync)
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """
    Hash a password with bcrypt. Returns a UTF-8 string like
    ``$2b$12$...`` — safe to store directly in the DB column.

    Raises ValueError on empty/non-string input; this is a programming
    error, not a user-facing condition, so we let it propagate.
    """
    if not isinstance(plain, str) or not plain:
        raise ValueError("password must be a non-empty string")

    tracer = get_tracer()
    with tracer.span("passwords.hash") as span:
        span.set_attribute("password_bytes", len(_truncate(plain)))

        try:
            salt = bcrypt.gensalt(rounds=12)
            hashed = bcrypt.hashpw(_truncate(plain), salt)
        except Exception as exc:
            span.set_error(f"{type(exc).__name__}: {exc}")
            raise

        # Never log the hash itself — only its length and prefix so
        # you can tell the scheme ($2b$) without seeing the secret.
        result = hashed.decode("utf-8")
        span.set_attribute("hash_length", len(result))
        span.set_attribute("hash_prefix", result[:7])    # "$2b$12$"
        return result


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    """
    Return True iff ``plain`` matches ``hashed``.

    Never raises. Every failure mode — None/empty hash, plaintext hash,
    malformed bcrypt string, encoding error — returns False. This
    means callers can write ``if not verify_password(...)`` without
    worrying about exception handling.

    The three cases handled:

      - ``hashed`` is None or empty          → False (row never hashed)
      - ``hashed`` is plaintext              → False + warning logged
      - ``hashed`` is a valid bcrypt string  → real verification
    """
    tracer = get_tracer()
    with tracer.span("passwords.verify") as span:

        if not plain or not hashed:
            span.set_attribute("matched", False)
            span.set_attribute("reason", "missing_input")
            return False

        if not isinstance(hashed, str):
            span.set_attribute("matched", False)
            span.set_attribute("reason", "non_string_hash")
            return False

        # A bcrypt hash always starts with $2a$, $2b$, or $2y$. If
        # it doesn't, the row was written by something other than
        # this function — usually a seed script that forgot to hash.
        if not hashed.startswith(("$2a$", "$2b$", "$2y$")):
            span.set_attribute("matched", False)
            span.set_attribute("reason", "not_a_bcrypt_hash")
            span.set_attribute("hash_prefix", hashed[:10])
            logger.warning(
                "verify_password: stored hash is not bcrypt "
                "(prefix={!r}). Seed script likely stored plaintext "
                "or wrote to the wrong column.",
                hashed[:10],
            )
            return False

        try:
            matched = bcrypt.checkpw(_truncate(plain), hashed.encode("utf-8"))
        except (ValueError, TypeError, AttributeError) as exc:
            # Malformed salt, wrong length, encoding failure — none of
            # these should crash the caller.
            span.set_attribute("matched", False)
            span.set_attribute("reason", f"bcrypt_error:{type(exc).__name__}")
            return False

        span.set_attribute("matched", matched)
        return matched


# ---------------------------------------------------------------------------
# Password hashing (async — use these in FastAPI routes)
# ---------------------------------------------------------------------------

async def ahash_password(plain: str) -> str:
    """Async wrapper — runs bcrypt in a thread, doesn't block the loop."""
    return await asyncio.to_thread(hash_password, plain)


async def averify_password(plain: str, hashed: Optional[str]) -> bool:
    """Async wrapper — runs bcrypt in a thread, doesn't block the loop."""
    return await asyncio.to_thread(verify_password, plain, hashed)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(user_id: str) -> str:
    """
    Create an HS256 JWT signed with JWT_SECRET.

    Claims:
      - sub: user_id
      - iat: issued-at (Unix seconds)
      - exp: expiry (Unix seconds), JWT_EXPIRES_MINUTES from now
    """
    if not user_id:
        raise ValueError("user_id must be a non-empty string")

    tracer = get_tracer()
    with tracer.span("jwt.create") as span:
        now = datetime.now(timezone.utc)
        exp = now + timedelta(minutes=JWT_EXPIRES_MINUTES)

        payload: Dict[str, Any] = {
            "sub": user_id,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

        span.set_attribute("user_id", user_id)
        span.set_attribute("expires_minutes", JWT_EXPIRES_MINUTES)
        span.set_attribute("token_length", len(token))
        return token


def decode_access_token(token: str) -> Optional[str]:
    """
    Return the user_id from a valid token, or None if the token is
    missing, expired, malformed, or signed with the wrong key.

    Never raises — the middleware relies on the None-on-invalid contract.
    """
    tracer = get_tracer()
    with tracer.span("jwt.decode") as span:

        if not token or not isinstance(token, str):
            span.set_attribute("valid", False)
            span.set_attribute("reason", "missing_token")
            return None

        try:
            payload = jwt.decode(
                token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
            )
        except jwt.ExpiredSignatureError:
            span.set_attribute("valid", False)
            span.set_attribute("reason", "expired")
            return None
        except jwt.InvalidTokenError as exc:
            span.set_attribute("valid", False)
            span.set_attribute("reason", f"invalid:{type(exc).__name__}")
            return None

        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            span.set_attribute("valid", False)
            span.set_attribute("reason", "no_sub")
            return None

        span.set_attribute("valid", True)
        span.set_attribute("user_id", user_id)
        return user_id