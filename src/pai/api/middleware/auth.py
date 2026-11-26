"""JWT authentication middleware."""
from __future__ import annotations

import re

from fastapi import Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from pai.security.tokens.auth import decode_access_token
from pai.common.constants import API_PREFIX

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
    f"{API_PREFIX}/health",
    f"{API_PREFIX}/auth/register",
    f"{API_PREFIX}/auth/login",
    f"{API_PREFIX}/auth/refresh",
    f"{API_PREFIX}/auth/forgot-password",
    f"{API_PREFIX}/auth/reset-password",
    "/settings",                                  # alias redirecting to the page below
    f"{API_PREFIX}/account/settings",             # the Settings page shell (no data; its API calls need a token)
    f"{API_PREFIX}/account/help",                 # the capabilities help page (static, no data)
    f"{API_PREFIX}/system/admin",
    f"{API_PREFIX}/system/admin/legacy",
}


# GET {API_PREFIX}/tasks/{session_id}/events (SSE). Nothing else accepts a query-string token.
_SSE_PATH = re.compile(rf"^{re.escape(API_PREFIX)}/tasks/[^/]+/events$")


# Static JS/CSS modules of the admin page's extra tabs. Its API calls (/api/v1/admin/*) still need an
# admin bearer token; only the files themselves are public.
_PUBLIC_PREFIXES: tuple[str, ...] = ("/admin/static/",)


def _normalize_path(path: str) -> str:
    stripped = path.rstrip("/")
    return stripped or "/"



class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = _normalize_path(request.url.path)
        method = request.method

        if method == "OPTIONS":
            return await call_next(request)

        if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
            logger.debug("authz: public {} {}", method, path)
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        token = None

        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):].strip()

        if not token:
            token = request.cookies.get("access_token")

        # EventSource cannot set headers: allow ?token= for the SSE status stream ONLY.
        if not token and method == "GET" and _SSE_PATH.match(path):
            token = request.query_params.get("token")

        if not token:
            logger.warning(
                "authz: reject {} {} — missing token",
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