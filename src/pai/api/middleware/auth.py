from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from pai.security.auth import decode_access_token


# ============================================================
# Public paths — matched EXACTLY
# ============================================================
#
# No prefix matching for auth. Every entry here is a complete path.
# If you add a new public endpoint, add its exact path.
#
_PUBLIC_PATHS: set[str] = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/health",
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/system/admin",     # HTML shell — no data
    "/ws",
    # Registry downloads (public — like an app store):
    "/api/v1/plugins/index.json",
}


def _is_public_registry_path(path: str) -> bool:
    """
    Registry download endpoints are public.

    These serve only signed metadata and SHA-256-verified binaries.
    They contain no user data and the client verifies integrity
    against the registry index before loading anything.

        /api/v1/plugins/{id}/{version}/manifest.json
        /api/v1/plugins/{id}/{version}/artifact/{platform}
    """
    if not path.startswith("/api/v1/plugins/"):
        return False

    # /api/v1/plugins/index.json is already in _PUBLIC_PATHS but
    # including it here is harmless.
    if path == "/api/v1/plugins/index.json":
        return True

    # Exclude the catalog endpoint — it's dynamic and belongs to
    # the authenticated app, not the download path.
    if path.startswith("/api/v1/plugins/catalog"):
        return False

    if path.endswith("/manifest.json"):
        return True

    if "/artifact/" in path:
        return True

    return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"

        # 1. CORS preflight and exact public paths.
        if request.method == "OPTIONS" or path in _PUBLIC_PATHS:
            return await call_next(request)

        # 2. Public registry downloads.
        if _is_public_registry_path(path):
            return await call_next(request)

        # 3. Everything else requires a Bearer token.
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"detail": "Missing or invalid token"},
                status_code=401,
            )

        token = auth_header[len("Bearer "):].strip()
        user_id = decode_access_token(token)
        if user_id is None:
            return JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401,
            )

        # 4. Attach identity to the request.
        #
        # Starlette's BaseHTTPMiddleware may not preserve
        # request.state across dispatch -> route, so we set it on
        # the scope dict directly. get_current_user reads through
        # request.state, which reads from scope["state"].
        if "state" not in request.scope:
            request.scope["state"] = {}
        request.scope["state"]["user_id"] = user_id
        request.scope["state"]["token"] = token

        # Belt-and-braces: set on the Request object too.
        request.state.user_id = user_id
        request.state.token = token

        return await call_next(request)