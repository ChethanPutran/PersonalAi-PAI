"""HTTP + WebSocket transport to the PAI backend.

The HTTP client logs in with email/password, caches the JWT, and
transparently re-logs in when the token is close to expiry (proactive)
or when a request returns 401 (reactive). The WebSocket client borrows
the API client's current token on every (re)connect, so a rotated
token is picked up automatically.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import websockets
from websockets.protocol import State

API_PREFIX = "/api/v1"      # the backend's REST prefix (this package never imports the backend)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _token_expired(token: str, leeway: float = 60.0) -> bool:
    """
    Return True if the JWT is expired, unparseable, has no `exp`, or
    expires within `leeway` seconds. We never trust a token that's about
    to expire — refreshing early is cheap, and clock skew is real.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return True
        payload_b64 = parts[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64 + padding)
        )
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)):
            return True
        return time.time() > (float(exp) - leeway)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# APIClient
# ---------------------------------------------------------------------------

class APIClient:
    """Async wrapper over the backend's REST surface, with auto-login."""

    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._token: Optional[str] = None
        self._user_id: Optional[str] = None
        # Sent as X-PAI-Device-Id on every call so the backend can tell "this device acting on
        # itself" (local) from "acting on another device" (needs that device's opt-in).
        self.device_id: Optional[str] = None

        # No default Authorization header — we set it per request so
        # rotated tokens are always used.
        self._http = httpx.AsyncClient(
            base_url=self._base, timeout=30.0
        )

        # Serialize concurrent logins. If two coroutines both detect an
        # expired token simultaneously, only one hits /auth/login.
        self._login_lock = asyncio.Lock()

    @property
    def user_id(self) -> Optional[str]:
        return self._user_id

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    @property
    def token(self) -> Optional[str]:
        return self._token

    async def login(self, retries: int = 30, delay: float = 2.0) -> str:
        """Force a fresh login. Returns the access token.

        Retries on transient failures:
        • Connection refused / timeouts / network errors
        • HTTP 5xx (backend not ready or crashed)
        • HTTP 429 (rate-limited)

        Fails immediately (no retry) on 4xx other than 429 — a wrong
        password or malformed payload will not succeed by waiting.

        Raises `RuntimeError` on both permanent and exhausted-retry failures
        so callers have a single, predictable exception type to catch.
        """
        async with self._login_lock:
            logger.info("Logging in as %s", self._email)
            last_error: Exception | None = None

            for attempt in range(1, retries + 1):
                # ── 1. Network call ──
                try:
                    r = await self._http.post(
                        "/auth/login",
                        json={"email": self._email, "password": self._password},
                        timeout=httpx.Timeout(10.0),
                    )
                except httpx.RequestError as e:
                    # ConnectError, ConnectTimeout, ReadTimeout,
                    # RemoteProtocolError, NetworkError, …
                    last_error = e
                    if attempt == retries:
                        break
                    logger.warning(
                        "Login attempt %d/%d failed (%s) — retrying in %.1fs",
                        attempt, retries, type(e).__name__, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                # ── 2. Permanent failures: fail fast ──
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    body = r.text[:300]
                    logger.error(
                        "Login rejected (%s): %s", r.status_code, body,
                    )
                    raise RuntimeError(
                        f"Login rejected ({r.status_code}): {body}"
                    )

                # ── 3. Transient failures: retry ──
                if r.status_code >= 500 or r.status_code == 429:
                    last_error = RuntimeError(
                        f"HTTP {r.status_code}: {r.text[:200]}"
                    )
                    if attempt == retries:
                        break
                    logger.warning(
                        "Login attempt %d/%d: HTTP %s — retrying in %.1fs",
                        attempt, retries, r.status_code, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                # ── 4. Success (2xx): parse and return ──
                try:
                    data = r.json()
                except ValueError as e:
                    raise RuntimeError(
                        f"Login returned non-JSON body: {r.text[:300]}"
                    ) from e

                token = data.get("access_token")
                if not token:
                    raise RuntimeError(
                        f"Login response missing access_token: {data!r}"
                    )

                self._token = token
                self._user_id = data.get("user_id")
                logger.info(
                    "Logged in as %s (admin=%s)",
                    data.get("email"), data.get("is_admin", False),
                )
                return token

            # ── All attempts exhausted (only reachable via transient errors) ──
            raise RuntimeError(
                f"Login failed after {retries} attempts: {last_error}"
            ) from last_error

                    
            

    async def ensure_token(self) -> str:
        """Return a valid token, logging in if the current one is missing/expired."""
        if self._token is None or _token_expired(self._token):
            return await self.login()
        return self._token

    # ------------------------------------------------------------------
    # Request helper — retries once on 401 after re-login
    # ------------------------------------------------------------------

    async def _request(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        await self.ensure_token()

        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._token}"
        if self.device_id:
            headers["X-PAI-Device-Id"] = self.device_id

        r = await self._http.request(
            method, url, headers=headers, **kwargs
        )
        if r.status_code == 401:
            logger.info(
                "401 on %s %s — re-logging in and retrying", method, url
            )
            await self.login()
            headers["Authorization"] = f"Bearer {self._token}"
            r = await self._http.request(
                method, url, headers=headers, **kwargs
            )

        r.raise_for_status()
        return r

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    async def register(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = await self._request(
            "POST", "/devices/register", json=payload
        )
        return r.json()

    async def heartbeat(self, device_id: str) -> None:
        # Backend endpoint is unauthenticated; still fine to send the header.
        await self._request(
            "POST", f"/devices/{device_id}/heartbeat", json={}
        )

    # ------------------------------------------------------------------
    # Plugin registry
    # ------------------------------------------------------------------

    async def fetch_plugin_info(
        self, plugin_id: str, version: Optional[str] = None
    ) -> Dict[str, Any]:
        """``GET /plugins/{id}[?version=]``: one catalog entry (with ``packageUrl``/``checksum``)."""
        params = {"version": version} if version else None
        r = await self._request("GET", f"/plugins/{plugin_id}", params=params)
        return r.json()

    @staticmethod
    def version_entry(info: Dict[str, Any]) -> Dict[str, Any]:
        """Per-version install data from a catalog entry: ``artifacts`` (platform -> package URL)
        and ``sha256`` (platform -> checksum), one key per platform the entry targets."""
        platforms = list((info.get("platforms") or {}).keys())
        url, sha = info.get("packageUrl"), info.get("checksum")
        if sha:                       # the backend sends "sha256:<hex>"; hashes here are bare hex
            sha = sha.removeprefix("sha256:").lower()
        return {
            "artifacts": {p: url for p in platforms if url},
            "sha256": {p: sha for p in platforms if sha},
        }

    async def fetch_index(self) -> Dict[str, Any]:
        """Registry index built from ``GET /plugins/catalog``.

        Shape: ``{"plugins": [{id, name, latest, versions: {ver: {artifacts, sha256}}}]}``.
        The catalog lists, per device of this user, the newest runnable version of each plugin,
        so ``versions`` holds those versions only; use :meth:`fetch_plugin_info` for another one.
        """
        r = await self._request("GET", "/plugins/catalog")
        merged: Dict[str, Dict[str, Any]] = {}
        for entries in (r.json() or {}).values():
            for info in entries:
                if not info.get("compatible", True):
                    continue
                entry = merged.setdefault(info["id"], {
                    "id": info["id"], "name": info.get("name"),
                    "latest": info["version"], "versions": {},
                })
                v = entry["versions"].setdefault(
                    info["version"], {"artifacts": {}, "sha256": {}})
                data = self.version_entry(info)
                v["artifacts"].update(data["artifacts"])
                v["sha256"].update(data["sha256"])
        return {"plugins": list(merged.values())}

    async def fetch_manifest(
        self, plugin_id: str, version: str
    ) -> Dict[str, Any]:
        r = await self._request(
            "GET", f"/plugins/{plugin_id}/manifest.json",
            params={"version": version},
        )
        return r.json()

    async def download_artifact(self, package_url: str, dest: Path) -> Path:
        """Stream a plugin package (``packageUrl`` from the catalog) to ``dest``.

        A backend-relative URL (``/api/v1/plugins/artifacts/...``) is fetched with our token; an
        absolute one (e.g. a presigned S3 URL) is fetched as is, never with our credentials.
        """
        if package_url.startswith("file:"):
            raise RuntimeError(f"cannot download a file:// package over HTTP: {package_url}")
        own = not package_url.startswith(("http://", "https://"))
        url = package_url.removeprefix(API_PREFIX) if own else package_url
        if own:
            await self.ensure_token()

        dest.parent.mkdir(parents=True, exist_ok=True)

        async def _stream() -> None:
            headers = {"Authorization": f"Bearer {self._token}"} if own else {}
            async with self._http.stream("GET", url, headers=headers) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)

        try:
            await _stream()
        except httpx.HTTPStatusError as exc:
            if own and exc.response.status_code == 401:
                await self.login()
                await _stream()
            else:
                raise
        return dest

    async def list_plugins(
        self,
        user_id: Optional[str] = None,
        platform: Optional[str] = None,
        architecture: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, str] = {}
        if user_id:
            params["user_id"] = user_id
        if platform:
            params["platform"] = platform
        if architecture:
            params["architecture"] = architecture
        r = await self._request("GET", "/plugins", params=params)
        return r.json()

    async def get_plugin(self, plugin_id: str) -> Dict[str, Any]:
        r = await self._request("GET", f"/plugins/{plugin_id}")
        return r.json()

    async def install_plugin(
        self,
        plugin_id: str,
        device_id: str,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"device_id": device_id}
        if version:
            body["version"] = version
        r = await self._request(
            "POST",
            f"/plugins/{plugin_id}/install",
            json=body,
        )
        return r.json()


    async def enable_plugin(
        self, plugin_id: str, device_id: Optional[str] = None
    ) -> Dict[str, Any]:
        body = {"device_id": device_id} if device_id else {}
        r = await self._request(
            "POST", f"/plugins/{plugin_id}/enable", json=body
        )
        return r.json()

    async def disable_plugin(
        self, plugin_id: str, device_id: Optional[str] = None
    ) -> Dict[str, Any]:
        body = {"device_id": device_id} if device_id else {}
        r = await self._request(
            "POST", f"/plugins/{plugin_id}/disable", json=body
        )
        return r.json()

    async def get_catalog(
        self, platform: str, architecture: str
    ) -> Dict[str, Any]:
        r = await self._request(
            "GET",
            "/plugins/catalog",
            params={"platform": platform, "architecture": architecture},
        )
        return r.json()

    async def plugin_system_status(self) -> Dict[str, Any]:
        r = await self._request("GET", "/plugins/system/status")
        return r.json()

    # ------------------------------------------------------------------
    # Device plugin state
    # ------------------------------------------------------------------

    async def list_installed(self, device_id: str) -> Dict[str, Any]:
        r = await self._request(
            "GET", f"/plugins/device/{device_id}/installed"
        )
        return r.json()

    async def reconcile(
        self, device_id: str, plugins: list[Dict[str, Any]]
    ) -> Dict[str, Any]:
        r = await self._request(
            "POST",
            f"/plugins/device/{device_id}/reconcile",
            json={"plugins": plugins},
        )
        return r.json()

    async def report_install(
        self, plugin_id: str, body: Dict[str, Any]
    ) -> None:
        await self._request(
            "POST",
            f"/plugins/{plugin_id}/report-install",
            json=body,
        )

    async def report_uninstall(
        self, plugin_id: str, body: Dict[str, Any]
    ) -> None:
        await self._request(
            "POST",
            f"/plugins/{plugin_id}/report-uninstall",
            json=body,
        )

    async def uninstall_plugin(
        self, plugin_id: str, device_id: str
    ) -> Dict[str, Any]:
        r = await self._request(
            "POST",
            f"/plugins/{plugin_id}/uninstall",
            json={"device_id": device_id},
        )
        return r.json()

    async def invoke_capability(
        self,
        device_id: str,
        capability: str,
        parameters: Optional[Dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> Dict[str, Any]:
        r = await self._request(
            "POST",
            f"/devices/{device_id}/invoke",
            json={
                "capability": capability,
                "parameters": parameters or {},
                "timeout_seconds": timeout,
            },
            timeout=timeout + 10.0,   # httpx timeout must exceed server timeout
        )
        return r.json()


# ---------------------------------------------------------------------------
# WSClient
# ---------------------------------------------------------------------------
class WSClient:
    """
    Reconnecting WebSocket client.

    Accepts the `APIClient` so it can pull the current token on every
    (re)connect. That means a rotated token — or a fresh login after
    the backend closed us with 4401 — is automatically used the next
    time we connect.
    """

    def __init__(self, ws_base: str, api_client: APIClient) -> None:
        self._base = ws_base.rstrip("/")
        self._api = api_client
        self._ws: Optional[websockets.asyncio.client.ClientConnection] = None
        self._send_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        # Updated to use the new State-based check for websockets 14+
        return self._ws is not None and self._ws.state is State.OPEN

    async def connect(self, device_id: str) -> None:
        token = await self._api.ensure_token()
        url = f"{self._base}/devices/{device_id}/ws?token={token}"
        self._ws = await websockets.connect(
            url, ping_interval=None, max_size=16 * 1024 * 1024
        )
        logger.info("WS connected: device=%s", device_id)

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def send(self, payload: Dict[str, Any]) -> None:
        if not self.connected:
            raise RuntimeError("WebSocket not connected")
        async with self._send_lock:
            await self._ws.send(json.dumps(payload))

    async def recv(self) -> Dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("WebSocket not connected")
        raw = await self._ws.recv()
        return json.loads(raw)