"""The device agent: login, registration, WebSocket, sync, dispatch.

``DeviceAgent`` is the top-level long-running component that runs on every
managed device. Its responsibilities, in execution order:

  * **Authentication** — log in with the configured credentials *first* so
    bad credentials fail fast, before we attempt registration.
  * **Plugin discovery** — load any plugins already present in the on-disk
    plugin root. Loading only populates the manager's in-memory registry;
    plugins are not started until the sync loop decides the backend wants
    them running.
  * **Registration** — call ``/device/register`` with a stable device
    identity. If a prior device id is persisted under ``state_dir/device_id``
    it is sent along so the backend can restore the same row.
  * **Transport** — open a WebSocket to the backend and react to close
    codes that mean "re-authenticate" (4401) or "stop the agent" (4003).
  * **Background loops** — heartbeat, WebSocket reconnect / message
    dispatch, and periodic state reconciliation with the backend.
  * **Graceful shutdown** — cancel background tasks, close the WS, stop
    plugins, and close the HTTP client when SIGINT/SIGTERM arrives.

Entry point: :meth:`DeviceAgent.run`.
"""
from __future__ import annotations

import asyncio
import logging
import platform as _platform
import signal
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from websockets.exceptions import ConnectionClosed

from ..config import (
    DeviceConfig,
    DeviceRegistrationInfo,
    RegisterRequest,
)
from ..plugins import DevicePluginManager
from ..transport import APIClient, WSClient, sha256_file
from pai_device.agent.capabilities import BuiltinCapabilities

logger = logging.getLogger(__name__)


# WebSocket close codes emitted by the backend that require special handling.
#
#   _WS_CLOSE_AUTH            -> the access token is stale / revoked;
#                                re-login and reconnect on the next iteration.
#   _WS_CLOSE_OWNERSHIP       -> this device id belongs to another user;
#                                the agent cannot recover, so stop entirely.
#   _WS_CLOSE_REGISTER_FAILED -> the backend could not register the device for
#                                this connection; back off and retry.
_WS_CLOSE_AUTH = 4401
_WS_CLOSE_OWNERSHIP = 4003
_WS_CLOSE_REGISTER_FAILED = 4500


class DeviceAgent:
    """Top-level device agent.

    Owns the HTTP client, WebSocket client, and plugin manager for the
    lifetime of the process. See the module docstring for the full
    lifecycle.
    """

    def __init__(self, config: DeviceConfig) -> None:
        """Build the agent's collaborators and prepare the state directory.

        No network I/O is performed here — the clients are constructed lazily
        and do their work on first call.
        """
        self.config = config

        # Persistent local state (device id, etc.) lives under ``state_dir``.
        self.state_dir = Path(config.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.device_id_file = self.state_dir / "device_id"

        # --- collaborators ---------------------------------------------------
        self.api = APIClient(
            config.api_base, config.email, config.password
        )
        self.ws = WSClient(config.ws_url, self.api)
        self.plugins = DevicePluginManager(config.plugin_dir)

        # --- runtime state ---------------------------------------------------
        self.device_id: Optional[str] = None
        # What the owner allows OTHER devices to do to this one. Fail closed: nothing is allowed
        # until the backend says so (in the ``connected`` frame, then ``device.remote_access``).
        self.remote_access: Dict[str, bool] = {"install": False, "invoke": False}
        # ``_stop`` is set by the signal handlers (or by the WS loop when the
        # backend sends a terminal close code) to tear everything down.
        self._stop = asyncio.Event()
        # Background asyncio.Tasks started by ``run``; cancelled on shutdown.
        self._background: list[asyncio.Task] = []
        self.builtins = BuiltinCapabilities()

        logger.debug(
            "DeviceAgent initialised (state_dir=%s, plugin_dir=%s)",
            self.state_dir, config.plugin_dir,
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main coroutine: authenticate, register, run loops until stopped."""
        # 1. Login first — surfaces bad credentials immediately, before
        #    we try to register a device.
        logger.info(
            "Logging in as %s against %s", self.config.email, self.config.api_base
        )
        try:
            await self.api.login()
        except Exception as exc:
            logger.error("Initial login failed: %s", exc)
            raise
        logger.info("Initial login succeeded")

        logger.info(
            "Built-in capabilities: %s", self.builtins.list(),
        )

        # 2. Load locally-installed plugins from disk.
        await self.plugins.load_existing()
        logger.info(
            "Loaded %d existing plugin(s) from %s",
            len(self.plugins.list_all()), self.config.plugin_dir,
        )

        # 3. Register (or restore) the device.
        await self._register()

        # 4. Signal handlers → graceful stop.
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:
                # Windows event loops do not support add_signal_handler.
                pass

        # 5. Background loops.
        logger.info("Starting background loops (ws, heartbeat, sync)")
        self._background.append(asyncio.create_task(self._ws_loop()))
        self._background.append(asyncio.create_task(self._heartbeat_loop()))
        self._background.append(asyncio.create_task(self._sync_loop()))

        # Block until someone sets the stop event (signal or fatal WS close).
        await self._stop.wait()

        logger.info("Shutting down device agent...")
        for t in self._background:
            t.cancel()
        for t in self._background:
            try:
                await t
            except asyncio.CancelledError:
                pass

        await self.ws.close()
        await self.plugins.shutdown()
        await self.api.close()
        logger.info("Device agent stopped")

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def _register(self) -> None:
        """Register this device with the backend, retrying on transient errors.

        If a ``device_id`` file exists from a previous run it is sent along in
        the payload so the backend can restore the same row (and thus the same
        device's plugin assignments) instead of creating a new one. The
        returned id is persisted to ``state_dir/device_id``.

        Retries up to 5 times with exponential backoff (2, 4, 8, 16, 32 s,
        capped at 60 s). Raises :class:`RuntimeError` if all attempts fail.
        """
        existing_id = None
        if self.device_id_file.exists():
            existing_id = self.device_id_file.read_text().strip() or None
            if existing_id:
                logger.debug(
                    "Found existing device_id on disk: %s", existing_id
                )
            else:
                logger.debug("device_id file present but empty; will register fresh")
        else:
            logger.debug("No existing device_id on disk; will register fresh")

        # Snapshot the local device metadata the backend stores alongside the
        # device row. Capabilities start empty — the sync loop reconciles them.
        info = DeviceRegistrationInfo(
            name=self.config.device_name,
            device_type=self.config.device_type,
            app_version=self.config.app_version,
            runtime_version=self.config.runtime_version,
            platform=self.config.platform,
            platform_version=_platform.version(),
            os_version=self.config.os_version,
            architecture=self.config.architecture,
            hostname=self.config.hostname,
            capabilities=[],
            metadata={"agent": "pai-device-agent/1.0"},
        )
        payload = RegisterRequest(device=info, device_id=existing_id)

        for attempt in range(1, 6):
            try:
                resp = await self.api.register(payload.model_dump())
                self.device_id = resp["device_id"]
                self.api.device_id = self.device_id       # X-PAI-Device-Id on every later call
                self.device_id_file.write_text(self.device_id)
                logger.info(
                    "Registered as device %s (%s)",
                    self.device_id, resp.get("message", ""),
                )
                return
            except Exception as exc:
                delay = min(60, 2 ** attempt)
                logger.warning(
                    "Registration attempt %d failed: %s (retry in %ds)",
                    attempt, exc, delay,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("Could not register with backend")

    # ------------------------------------------------------------------
    # WebSocket loop
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Maintain the WebSocket connection, reconnecting as needed.

        Handles the two terminal close codes by either re-logging in (4401)
        or stopping the whole agent (4003). Any other close results in an
        exponential-backoff reconnect attempt.
        """
        backoff = self.config.reconnect_min
        need_relogin = False

        while not self._stop.is_set():
            # If the previous attempt closed with 4401, refresh the token
            # before reconnecting; back off if the re-login itself fails.
            if need_relogin:
                try:
                    await self.api.login()
                    need_relogin = False
                    logger.info("Re-login successful")
                except Exception as exc:
                    logger.error(
                        "Re-login failed: %s — retrying in %.1fs",
                        exc, backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(
                        self.config.reconnect_max, backoff * 2
                    )
                    continue

            try:
                logger.debug("Opening WebSocket to %s", self.config.ws_url)
                await self.ws.connect(self.device_id)
                backoff = self.config.reconnect_min
                logger.info("WebSocket connected (device_id=%s)", self.device_id)

                # Announce ourselves; the backend uses this to mark the
                # device as online with a fresh agent version.
                await self.ws.send({
                    "type": "event",
                    "event": "agent_ready",
                    "agent_version": self.config.app_version,
                })

                await self._ws_message_loop()

            except ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd else None

                if code == _WS_CLOSE_AUTH:
                    # Token expired / revoked: fetch a new one and reconnect.
                    logger.warning(
                        "WS auth rejected (4401) — re-login on next attempt"
                    )
                    need_relogin = True
                    continue

                if code == _WS_CLOSE_OWNERSHIP:
                    # Fatal: this device id is claimed by another user.
                    logger.error(
                        "WS ownership rejected (4003) — device belongs "
                        "to a different user. Stopping agent."
                    )
                    self._stop.set()
                    return

                if code == _WS_CLOSE_REGISTER_FAILED:
                    # Transient (backend couldn't register this WS session);
                    # fall through to the backoff path below.
                    logger.error(
                        "WS device-register failed (4500) — backing off"
                    )

                logger.warning("WS closed (code=%s): %s", code, exc)

            except Exception:
                logger.exception("WS loop error")

            if self._stop.is_set():
                return

            logger.info("Reconnecting WS in %.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(self.config.reconnect_max, backoff * 2)

    async def _ws_message_loop(self) -> None:
        """Drain incoming messages from the WS until disconnect or stop.

        Each message is handled independently inside a try/except so a bug
        in one handler cannot take down the receive loop.
        """
        while not self._stop.is_set():
            message = await self.ws.recv()
            logger.debug("WS recv: type=%r", message.get("type"))
            try:
                await self._handle_ws_message(message)
            except Exception:
                logger.exception("Failed to handle WS message: %s", message)

    def _resolve_artifact(
        self, vinfo: Dict[str, Any]
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Return (platform_key, sha256) for the best-installable artifact.

        Uses the same resolution order as _install_plugin:
        1. the device's own platform
        2. "python" (server devices only)
        3. any other platform the manifest advertises

        Returns (None, None) if nothing is installable for this device.
        """
        artifacts = vinfo.get("artifacts") or {}
        shas = vinfo.get("sha256") or {}

        candidates: list[str] = []
        def _add(p: Optional[str]) -> None:
            if p and p not in candidates:
                candidates.append(p)

        _add(self.config.platform)
        if self.config.device_type == "server":
            _add("python")
        for p in artifacts.keys():
            _add(p)

        for plat in candidates:
            if plat not in artifacts:
                continue
            if plat == "python" and self.config.device_type != "server":
                continue
            return plat, shas.get(plat)
        return None, None

    async def _handle_ws_message(self, message: Dict[str, Any]) -> None:
        """Dispatch a decoded WS message to the appropriate handler."""
        msg_type = message.get("type")

        if msg_type == "connected":
            logger.info("WS handshake complete for %s", self.device_id)
            self._apply_remote_access(message.get("remote_access"))
            return

        if msg_type == "device.remote_access":
            self._apply_remote_access(message.get("remote_access"))
            return

        # Trivial control messages — nothing to do but acknowledge.
        if msg_type == "pong":
            return

        if msg_type == "heartbeat_ack":
            return

        # Plugin lifecycle commands.
        if msg_type == "plugin.install":
            await self._handle_install(message)
            return

        if msg_type == "plugin.uninstall":
            await self._handle_uninstall(message)
            return

        # Backend-initiated liveness check.
        if msg_type == "ping":
            await self.ws.send({"type": "pong", "device_id": self.device_id})
            return

        # Task dispatch: either the capability-shaped message or the legacy
        # ``type=task`` envelope.
        if message.get("request_id") and message.get("capability"):
            await self._handle_task(message)
            return

        if msg_type == "task" and message.get("request_id"):
            await self._handle_task(message)
            return

        logger.warning("Unhandled WS message: %s", message)

    # ------------------------------------------------------------------
    # Install / uninstall
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Remote access (the owner's switches for this device)
    # ------------------------------------------------------------------

    def _apply_remote_access(self, raw: Optional[Dict[str, Any]]) -> None:
        if not isinstance(raw, dict):
            return
        self.remote_access = {"install": bool(raw.get("install")), "invoke": bool(raw.get("invoke"))}
        logger.info("Remote access on this device: install=%s invoke=%s",
                    self.remote_access["install"], self.remote_access["invoke"])

    def _is_remote(self, source_device_id: Optional[str]) -> bool:
        """Anything not asked by this device itself is remote."""
        return not source_device_id or source_device_id != self.device_id

    async def _progress(self, operation_id: Optional[str], status: str, **extra: Any) -> None:
        """Tell the backend how far an install/uninstall got (best effort; the final result also
        goes through the report endpoints)."""
        if not operation_id:
            return
        try:
            await self.ws.send({"type": "plugin.progress", "operation_id": operation_id,
                                "status": status, **extra})
        except Exception:
            logger.debug("progress frame %s not sent", status, exc_info=True)

    # ------------------------------------------------------------------
    # Install / uninstall
    # ------------------------------------------------------------------

    async def _handle_install(self, message: Dict[str, Any]) -> None:
        """Handle a ``plugin.install`` command from the backend.

        Refuses a remote request unless the owner allowed remote installs on this device, then
        downloads, verifies and installs, reporting each stage (``plugin.progress``) and the final
        result (``/report-install``).
        """
        plugin_meta = message.get("plugin") or {}
        plugin_id = plugin_meta.get("id")
        version = plugin_meta.get("version")
        op_id = message.get("operation_id")
        if not plugin_id or not version:
            logger.warning("Malformed plugin.install: %s", message)
            await self._progress(op_id, "failed", error="Malformed install request")
            return

        if self._is_remote(message.get("requested_by_device")) and not self.remote_access["install"]:
            logger.warning("Refused remote install of %s: remote install is off on this device", plugin_id)
            err = "Remote plugin install is turned off on this device"
            await self._progress(op_id, "failed", error=err, error_code="remote_install_disabled")
            await self._report_install_failure(plugin_id, version, op_id, err)
            return

        logger.info("Installing plugin %s@%s", plugin_id, version)
        try:
            async def stage(name: str) -> None:
                await self._progress(op_id, name)

            await self._install_plugin(plugin_id, version, stage=stage)
            await self.api.report_install(plugin_id, {
                "device_id": self.device_id,
                "operation_id": op_id,
                "version": version,
                "success": True,
                "artifact_sha256": self._sha_for(plugin_id),
            })
            logger.info("Install complete: %s@%s", plugin_id, version)
        except Exception as exc:
            logger.exception("Install %s failed", plugin_id)
            await self._report_install_failure(plugin_id, version, op_id, str(exc)[:500])

    async def _report_install_failure(
        self, plugin_id: str, version: str, op_id: Optional[str], error: str,
    ) -> None:
        try:
            await self.api.report_install(plugin_id, {
                "device_id": self.device_id, "operation_id": op_id, "version": version,
                "success": False, "error": error,
            })
        except Exception:
            logger.exception("Could not report the install failure of %s", plugin_id)
            await self._progress(op_id, "failed", error=error)

    async def _handle_uninstall(self, message: Dict[str, Any]) -> None:
        """Handle a ``plugin.uninstall`` command from the backend."""
        plugin_id = (
            (message.get("plugin") or {}).get("id")
            or message.get("plugin_id")
        )
        op_id = message.get("operation_id")
        if not plugin_id:
            return
        if self._is_remote(message.get("requested_by_device")) and not self.remote_access["install"]:
            logger.warning("Refused remote uninstall of %s: remote install is off on this device", plugin_id)
            err = "Remote plugin install is turned off on this device"
            await self._progress(op_id, "failed", error=err, error_code="remote_install_disabled")
            await self.api.report_uninstall(plugin_id, {
                "device_id": self.device_id, "operation_id": op_id, "success": False, "error": err})
            return
        logger.info("Uninstalling plugin %s", plugin_id)
        await self._progress(op_id, "installing")            # "working on it"
        try:
            await self.plugins.uninstall(plugin_id)
            await self.api.report_uninstall(plugin_id, {
                "device_id": self.device_id,
                "operation_id": op_id,
                "success": True,
            })
            logger.info("Uninstall complete: %s", plugin_id)
        except Exception as exc:
            logger.exception("Uninstall %s failed", plugin_id)
            await self.api.report_uninstall(plugin_id, {
                "device_id": self.device_id,
                "operation_id": op_id,
                "success": False,
                "error": str(exc)[:500],
            })

    # ------------------------------------------------------------------
    # Task dispatch
    # ------------------------------------------------------------------

    async def _handle_task(self, message: Dict[str, Any]) -> None:
        """Dispatch a capability invocation to the plugin manager.

        The result (or error) is sent back to the backend on the same WS
        connection, keyed by ``request_id``.
        """
        request_id = message["request_id"]
        capability = message.get("capability")
        parameters = message.get("parameters") or {}

        if self._is_remote(message.get("source_device_id")) and not self.remote_access["invoke"]:
            logger.warning("Refused remote task %s (%s): remote use is off on this device",
                           request_id, capability)
            await self.ws.send({
                "type": "result",
                "request_id": request_id,
                "error": "Remote plugin use is turned off on this device.",
                "success": False,
                "result": {"ok": False, "error": "Remote plugin use is turned off on this device."},
            })
            return

        if not capability:
            logger.warning("Task %s missing capability", request_id)
            await self.ws.send({
                "type": "result",
                "request_id": request_id,
                "result": {"ok": False, "error": "Missing capability"},
            })
            return

        logger.info(
            "Dispatching task %s (capability=%s)", request_id, capability
        )
        try:
            if self.builtins.handles(capability):
                result = await self.builtins.invoke(
                    capability, parameters,
                )
                payload = {"ok": True, "data": result}
            else:
                result = await self.plugins.invoke(capability, parameters)
                payload = {"ok": True, "data": result}
                logger.info("Task %s completed (capability=%s) result: %s", request_id, capability, result)
        except Exception as exc:
            logger.exception("Task %s failed", request_id)
            payload = {"ok": False, "error": str(exc)}
            logger.info("Task %s failed (capability=%s) error: %s", request_id, capability, str(exc))

        await self.ws.send({
            "type": "result",
            "request_id": request_id,
            "result": payload,
        })

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Send periodic liveness signals to the backend.

        Prefers the WS path when connected (cheap, ordered with other
        traffic); otherwise falls back to the HTTP heartbeat so the backend's
        ``last_seen`` stays fresh during reconnects.
        """
        while not self._stop.is_set():
            await asyncio.sleep(self.config.heartbeat_interval)
            try:
                if self.ws.connected:
                    await self.ws.send({
                        "type": "heartbeat",
                        "device_id": self.device_id,
                    })
                    logger.debug("WS heartbeat sent")
                else:
                    await self.api.heartbeat(self.device_id)
                    logger.debug("HTTP heartbeat sent")
            except Exception:
                logger.debug("Heartbeat failed", exc_info=True)

    async def _sync_loop(self) -> None:
        """Run :meth:`_sync_once` on a fixed schedule until stopped.

        A small initial delay lets the WS handshake complete first so the
        first reconcile does not race with the initial state push.
        """
        await asyncio.sleep(2.0)
        while not self._stop.is_set():
            try:
                await self._sync_once()
            except Exception:
                logger.exception("Sync failed")
            await asyncio.sleep(self.config.reconcile_interval)

    async def _sync_once(self) -> None:
        """
        Reconcile local plugin state with the backend's `device_plugins` table.

        Actions per tick, per plugin:
        1. backend knows, enabled, not installed          → install
        2. backend knows, enabled, installed, drift       → re-install
        3. backend knows, enabled, installed, no drift    → start if stopped
        4. backend knows, disabled, running               → stop
        5. backend doesn't know, local install exists     → uninstall
        6. after all of the above: report actual state via reconcile

        Runs every `reconcile_interval` (60s by default). Safe to call
        repeatedly; each branch is idempotent.
        """
        logger.debug("Sync tick started for device %s", self.device_id)
        backend = await self.api.list_installed(self.device_id)
        backend_plugins = backend.get("plugins", [])
        backend_map = {p["plugin_id"]: p for p in backend_plugins}
        logger.debug(
            "Backend knows %d plugin(s): %s",
            len(backend_map), sorted(backend_map),
        )

        # ---- lazy registry index cache (one fetch per tick, only if needed)
        index_cache: Optional[Dict[str, Any]] = None
        async def _index_entry(pid: str) -> Optional[Dict[str, Any]]:
            nonlocal index_cache
            if index_cache is None:
                try:
                    index_cache = await self.api.fetch_index()
                except Exception as exc:
                    logger.warning("fetch_index failed: %s", exc)
                    index_cache = {"plugins": []}
            for p in index_cache.get("plugins", []):
                if p["id"] == pid:
                    return p
            return None

        # ------------------------------------------------------------------
        # Pass 1: per-plugin reconciliation against the backend
        # ------------------------------------------------------------------
        for pid, entry in backend_map.items():
            should_run = bool(entry.get("enabled_on_device", False))
            backend_version = entry.get("version")
            installed = self.plugins.has(pid)
            running = self.plugins.is_running(pid)

            # --- 4. disabled on backend: stop if running ---
            if not should_run:
                if running:
                    logger.info("Sync stop %s (disabled on backend)", pid)
                    try:
                        await self.plugins.stop(pid)
                    except Exception as exc:
                        logger.error("Sync stop %s failed: %s", pid, exc)
                continue

            # --- 1. enabled, not installed: install ---
            if not installed:
                idx = await _index_entry(pid)
                version = backend_version or (idx or {}).get("latest")
                if not version:
                    logger.warning(
                        "Cannot install %s: backend has no version and "
                        "plugin is not in registry index", pid,
                    )
                    continue
                try:
                    await self._install_plugin(pid, version)
                except Exception as exc:
                    logger.error(
                        "Sync install %s@%s failed: %s", pid, version, exc
                    )
                continue

            # --- installed: find local record for drift checks ---
            local = next(
                (p for p in self.plugins.list_all() if p.id == pid), None
            )
            if local is None:
                # has() said True but list_all() didn't include it —
                # shouldn't happen, but be defensive.
                logger.warning(
                    "Sync inconsistency: has(%s)=True but not in list_all()",
                    pid,
                )
                continue

            idx = await _index_entry(pid)
            target_version = backend_version or (idx or {}).get("latest")

            # --- 2a. version drift: local != backend target ---
            if target_version and target_version != local.version:
                logger.info(
                    "Version drift for %s: local=%s backend=%s — reinstalling",
                    pid, local.version, target_version,
                )
                try:
                    await self._install_plugin(pid, target_version)
                except Exception as exc:
                    logger.error(
                        "Drift reinstall %s@%s failed: %s",
                        pid, target_version, exc,
                    )
                continue

            # --- 2b. sha drift at same version ---
            if idx and local.version:
                vinfo = (idx.get("versions") or {}).get(local.version) or {}
                _, expected_sha = self._resolve_artifact(vinfo)
                if (
                    expected_sha
                    and local.sha256
                    and expected_sha != local.sha256
                ):
                    logger.info(
                        "Sha drift for %s@%s: local=%.12s backend=%.12s "
                        "— reinstalling",
                        pid, local.version, local.sha256, expected_sha,
                    )
                    try:
                        await self._install_plugin(pid, local.version)
                    except Exception as exc:
                        logger.error(
                            "Sha-drift reinstall %s@%s failed: %s",
                            pid, local.version, exc,
                        )
                    continue

            # --- 3. no drift: ensure running ---
            if not running:
                try:
                    await self.plugins.start(pid)
                except Exception as exc:
                    logger.error("Sync start %s failed: %s", pid, exc)

        # ------------------------------------------------------------------
        # Pass 2: uninstall local plugins the backend no longer knows about
        # ------------------------------------------------------------------
        for pid in list(self.plugins.list_ids()):
            if pid in backend_map:
                continue
            logger.info("Sync uninstall %s (not in backend)", pid)
            try:
                await self.plugins.uninstall(pid)
            except Exception as exc:
                logger.error("Sync uninstall %s failed: %s", pid, exc)
                continue
            try:
                await self.api.report_uninstall(pid, {
                    "device_id": self.device_id,
                    "success": True,
                })
            except Exception as exc:
                # report_uninstall can 404 if the backend already forgot
                # about this plugin. That's fine — nothing to reconcile.
                logger.warning(
                    "report_uninstall %s failed (continuing): %s", pid, exc
                )

        # ------------------------------------------------------------------
        # Pass 3: report actual state
        # ------------------------------------------------------------------
        reports = [
            {
                "id": p.id,
                "version": p.version,
                "enabled": p.running,
                "sha256": p.sha256,
            }
            for p in self.plugins.list_all()
        ]
        try:
            await self.api.reconcile(self.device_id, reports)
            logger.debug("Sync tick complete (%d plugin(s) reported)", len(reports))
        except Exception as exc:
            logger.warning("reconcile failed: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _install_plugin(
        self, plugin_id: str, version: str,
        stage: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        """Download, verify, and install a single plugin version.

        Steps:
          1. Fetch the manifest and registry index for ``plugin_id``.
          2. Pick the best platform artifact to install (see
             ``_resolve_artifact`` for the resolution order).
          3. Download the artifact to a temp location, verify its sha256,
             and hand it to the plugin manager, which moves it into place
             and starts the plugin.
        """
        logger.debug("Fetching manifest for %s@%s", plugin_id, version)
        manifest = await self.api.fetch_manifest(plugin_id, version)
        info = await self.api.fetch_plugin_info(plugin_id, version)
        vinfo = self.api.version_entry(info)
        if not vinfo["artifacts"]:
            raise RuntimeError(f"{plugin_id}@{version} has no downloadable package")

        # NOTE: The following prints are developer-facing debug output. They
        # are kept verbatim to avoid changing behaviour; a future refactor
        # could replace them with logger.info() calls at the same points.
        print(f"Installing {plugin_id}@{version} on {self.config.platform}...")
        print(f"  - vinfo: {vinfo}")
        print(f"  - {manifest.get('name')} — {manifest.get('description')}")
        print(f"  - capabilities: {manifest.get('capabilities')}")
        print(f"  - platforms: {manifest.get('platforms')}")
        print(f"  - architectures: {manifest.get('architectures')}")
        print(f"  - device_types: {manifest.get('device_types')}")

        # ---- resolve which platform artifact to download ----
        candidates: list[str] = []

        def _add(p: Optional[str]) -> None:
            if p and p not in candidates:
                candidates.append(p)

        _add(self.config.platform)
        if self.config.device_type == "server":
            _add("python")
        for p in vinfo["artifacts"].keys():
            _add(p)

        effective_platform: Optional[str] = None
        expected_sha: Optional[str] = None
        for plat in candidates:
            if plat not in vinfo["artifacts"]:
                continue
            if plat == "python" and self.config.device_type != "server":
                continue
            effective_platform = plat
            expected_sha = vinfo["sha256"].get(plat)
            break

        if effective_platform is None:
            raise RuntimeError(
                f"No artifact for {plugin_id}@{version} installable on "
                f"platform={self.config.platform!r} "
                f"device_type={self.config.device_type!r}; "
                f"manifest declares {list(vinfo['artifacts'].keys())}"
            )

        logger.debug(
            "Resolved artifact for %s@%s: platform=%s",
            plugin_id, version, effective_platform,
        )

        # ---- take the extension from the manifest's artifact path ----
        manifest_platforms = manifest.get("platforms") or {}
        platform_entry = manifest_platforms.get(effective_platform) or {}
        artifact_rel = platform_entry.get("artifact") or ""
        suffix = Path(artifact_rel).suffix or ".bin"

        tmp = (
            self.config.plugin_dir
            / "_downloads"
            / f"{plugin_id}-{version}{suffix}"
        )
        if stage:
            await stage("downloading")
        await self.api.download_artifact(
            vinfo["artifacts"][effective_platform], tmp
        )
        logger.debug("Downloaded %s@%s to %s", plugin_id, version, tmp)

        if stage:
            await stage("verifying")
        actual = sha256_file(tmp)
        if expected_sha and actual != expected_sha:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA256 mismatch: expected {expected_sha}, got {actual}"
            )
        logger.debug("SHA256 verified for %s@%s", plugin_id, version)

        if stage:
            await stage("installing")
        await self.plugins.install(
            plugin_id, version, manifest, tmp, expected_sha
        )

    def _sha_for(self, plugin_id: str) -> Optional[str]:
        """Return the recorded sha256 for an installed plugin, or None."""
        for p in self.plugins.list_all():
            if p.id == plugin_id:
                return p.sha256
        return None