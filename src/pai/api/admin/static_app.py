"""Static hosting of the admin page's extra-tab assets at /admin/static (ES modules, no CDN)."""
from __future__ import annotations

from pathlib import Path

from starlette.staticfiles import StaticFiles
from starlette.types import Scope

ADMIN_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Strict CSP sent with the asset files: only our own files, no inline style/script. connect-src 'self' lets the panel call the API on the same origin.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


class AdminStaticFiles(StaticFiles):
    """StaticFiles + security headers + cache policy (HTML revalidates, assets cache briefly)."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        is_html = path in ("", ".", "index.html") or path.endswith(".html")
        response.headers["Cache-Control"] = "no-cache" if is_html else "public, max-age=300"
        return response
