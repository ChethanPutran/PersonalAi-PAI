"""JWT authentication middleware."""
from __future__ import annotations

from fastapi import Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from pai.security.auth import decode_access_token


# ============================================================
# Public paths — exact match after trailing-slash normalization
# ============================================================
_PUBLIC_PATHS: set[str] = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/api/v1/health",
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/system/admin",
    "/api/v1/plugins/index.json",
}


def _normalize_path(path: str) -> str:
    stripped = path.rstrip("/")
    return stripped or "/"


def _is_public_registry_path(path: str) -> bool:
    """Registry downloads are public — signed metadata + verified binaries."""
    if not path.startswith("/api/v1/plugins/"):
        return False
    if path == "/api/v1/plugins/index.json":
        return True
    if path.startswith("/api/v1/plugins/catalog"):
        return False
    if path.endswith("/manifest.json"):
        return True
    if "/artifact/" in path:
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = _normalize_path(request.url.path)
        method = request.method

        if method == "OPTIONS":
            return await call_next(request)

        if path in _PUBLIC_PATHS or _is_public_registry_path(path):
            logger.debug("authz: public {} {}", method, path)
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning(
                "authz: reject {} {} — missing bearer token",
                method, path,
            )
            return JSONResponse(
                {
                    "detail": "Missing or invalid token",
                    "code": "missing_token",
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[len("Bearer "):].strip()
        user_id = decode_access_token(token)
        if user_id is None:
            logger.warning(
                "authz: reject {} {} — invalid or expired token",
                method, path,
            )
            return JSONResponse(
                {
                    "detail": "Invalid or expired token",
                    "code": "invalid_token",
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        logger.debug(
            "authz: allow {} {} user={}…",
            method, path, user_id[:8],
        )

        if "state" not in request.scope:
            request.scope["state"] = {}
        request.scope["state"]["user_id"] = user_id
        request.scope["state"]["token"] = token
        request.state.user_id = user_id
        request.state.token = token

        return await call_next(request)