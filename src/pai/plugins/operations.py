"""Remote plugin install / uninstall as a tracked operation.

Why: the old flow answered "install requested" and left every client to poll
``GET /plugins/device/{id}/installed`` until something changed. That cannot show progress, cannot
tell "the device never answered" from "still working", and surfaces raw errors. Here the server
owns an *operation*:

    requested -> delivered -> downloading -> verifying -> installing [-> awaiting_permission]
              -> succeeded | failed(code, message, hint)

* The target device reports progress (WebSocket ``plugin.progress`` frames; the final result also
  arrives through ``POST /plugins/{id}/report-install``). Status only moves forward; a finished
  operation never changes.
* Every change is pushed to the owner's connected devices (``{"type": "plugin.operation", ...}``
  on their existing device WebSocket) and published on the event bus, so clients never poll.
* A watchdog fails an operation whose device went offline or fell silent, with a clear reason,
  instead of leaving the user waiting for a client-side timeout.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from loguru import logger
from sqlalchemy import select

from pai.event_bus.events import EventType
from pai.storage.models import PluginOperationModel

REQUESTED, DELIVERED, DOWNLOADING, VERIFYING, INSTALLING = (
    "requested", "delivered", "downloading", "verifying", "installing")
AWAITING_PERMISSION, SUCCEEDED, FAILED = "awaiting_permission", "succeeded", "failed"

ACTIVE_STATUSES = (REQUESTED, DELIVERED, DOWNLOADING, VERIFYING, INSTALLING, AWAITING_PERMISSION)
TERMINAL_STATUSES = (SUCCEEDED, FAILED)
_EARLY = (REQUESTED, DELIVERED)        # a device that already started never goes back to these

PUSH_FRAME_TYPE = "plugin.operation"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# --------------------------------------------------------------------------------------
# Failure text: stable code + plain words + what to do. Raw device text stays in error_detail.
# --------------------------------------------------------------------------------------
_FAILURES: List[Tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"sha-?256 mismatch|checksum", re.I), "sha_mismatch",
     "The downloaded plugin package didn't match its checksum, so it was not installed.",
     "Try again. If it keeps happening, the package on the server may be damaged."),
    (re.compile(r"no artifact|not installable on|no downloadable package", re.I), "no_package_for_device",
     "There is no build of this plugin for that device.",
     "Pick a plugin version that supports the device's platform."),
    (re.compile(r"incompatible|requires app|minappversion|minpaiversion", re.I), "incompatible_version",
     "This plugin version can't run on that device.",
     "Update the PAI app on the device, or install an older plugin version."),
    (re.compile(r"permission", re.I), "permission_needed",
     "The device needs you to allow a permission before the plugin can be used.",
     "Open the PAI app on that device and allow the permission the plugin asks for."),
    (re.compile(r"not connected|disconnected|offline", re.I), "device_offline",
     "The device went offline.",
     "Open the PAI app on that device, make sure it is connected, then try again."),
    (re.compile(r"timed? ?out|timeout", re.I), "device_timeout",
     "The device took too long to finish.",
     "Check its network connection and try again."),
    (re.compile(r"disk|space|no space", re.I), "no_space",
     "The device ran out of storage.", "Free some space on that device and try again."),
]


def describe_failure(raw: Optional[str]) -> Tuple[str, str, str]:
    """(code, message, hint) for a raw error string from a device."""
    text = (raw or "").strip()
    for pattern, code, message, hint in _FAILURES:
        if pattern.search(text):
            return code, message, hint
    return ("install_failed", "The device couldn't finish the installation.",
            "Try again. If it keeps failing, check the device's agent log.")


def view_of(row: PluginOperationModel) -> Dict[str, Any]:
    """What clients see (never raw device text; that stays in ``error_detail``)."""
    failed = row.status == FAILED
    return {
        "id": row.id, "plugin_id": row.plugin_id, "device_id": row.device_id,
        "version": row.version, "kind": row.kind, "status": row.status,
        "terminal": row.status in TERMINAL_STATUSES,
        "requested_by_device": row.requested_by_device,
        "error": ({"code": row.error_code, "message": row.error_message, "hint": row.error_hint}
                  if failed else None),
        "created_at": _aware(row.created_at).isoformat() if row.created_at else None,
        "updated_at": _aware(row.updated_at).isoformat() if row.updated_at else None,
    }


class PluginOperations:
    STALL_SECONDS = 120.0           # no progress from the device for this long => failed
    OFFLINE_GRACE_SECONDS = 20.0    # device disconnected for this long => failed
    HARD_LIMIT_SECONDS = 900.0      # nothing runs longer than this
    TICK_SECONDS = 5.0

    def __init__(self, *, sessions: Any, device_manager: Any, event_bus: Any = None) -> None:
        self._sessions = sessions
        self._devices = device_manager
        self._bus = event_bus
        self._watchers: Dict[str, asyncio.Task] = {}
        self._offline_since: Dict[str, float] = {}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Operations left running by a previous process can never finish: close them."""
        async def _do(s):
            rows = (await s.execute(select(PluginOperationModel).where(
                PluginOperationModel.status.in_(ACTIVE_STATUSES)))).scalars().all()
            for r in rows:
                self._finish(r, FAILED, "server_restarted",
                             "The server restarted before the device reported back.",
                             "Check the plugin's state on the device, then try again.", None)
            return len(rows)
        n = await self._run(_do)
        if n:
            logger.info("PluginOperations: closed {} operation(s) left by a previous run", n)

    async def stop(self) -> None:
        tasks, self._watchers = list(self._watchers.values()), {}
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, fn):
        async with self._sessions.session() as s:
            return await fn(s)

    # ------------------------------------------------------------------ create / read
    async def create(self, *, user_id: str, device_id: str, plugin_id: str, version: Optional[str],
                     kind: str, requested_by_device: Optional[str] = None) -> Dict[str, Any]:
        row = PluginOperationModel(
            id=uuid.uuid4().hex, user_id=user_id, device_id=device_id, plugin_id=plugin_id,
            version=version, kind=kind, status=REQUESTED, requested_by_device=requested_by_device)

        async def _do(s):
            # A newer request replaces an older one still open for the same target.
            for old in (await s.execute(select(PluginOperationModel).where(
                    PluginOperationModel.device_id == device_id,
                    PluginOperationModel.plugin_id == plugin_id,
                    PluginOperationModel.status.in_(ACTIVE_STATUSES)))).scalars():
                self._finish(old, FAILED, "superseded", "A newer request replaced this one.", "", None)
            s.add(row)
            await s.flush()
            return view_of(row)

        view = await self._run(_do)
        await self._notify(user_id, view)
        self._watch(row.id, user_id)
        return view

    async def get(self, op_id: str, *, user_id: str) -> Optional[Dict[str, Any]]:
        async def _do(s):
            row = await s.get(PluginOperationModel, op_id)
            return view_of(row) if row is not None and row.user_id == user_id else None
        return await self._run(_do)

    async def list(self, *, user_id: str, device_id: Optional[str] = None,
                   plugin_id: Optional[str] = None, active_only: bool = False,
                   limit: int = 50) -> List[Dict[str, Any]]:
        async def _do(s):
            q = select(PluginOperationModel).where(PluginOperationModel.user_id == user_id)
            if device_id:
                q = q.where(PluginOperationModel.device_id == device_id)
            if plugin_id:
                q = q.where(PluginOperationModel.plugin_id == plugin_id)
            if active_only:
                q = q.where(PluginOperationModel.status.in_(ACTIVE_STATUSES))
            q = q.order_by(PluginOperationModel.created_at.desc()).limit(max(1, min(limit, 200)))
            return [view_of(r) for r in (await s.execute(q)).scalars()]
        return await self._run(_do)

    async def active_for(self, device_id: str, plugin_id: str) -> Optional[str]:
        """Id of the newest still-open operation for this target (for the REST report path)."""
        async def _do(s):
            row = (await s.execute(select(PluginOperationModel).where(
                PluginOperationModel.device_id == device_id,
                PluginOperationModel.plugin_id == plugin_id,
                PluginOperationModel.status.in_(ACTIVE_STATUSES))
                .order_by(PluginOperationModel.created_at.desc()).limit(1))).scalars().first()
            return row.id if row else None
        return await self._run(_do)

    # ------------------------------------------------------------------ transitions
    async def progress(self, op_id: str, *, device_id: str, status: str,
                       error: Optional[str] = None, error_code: Optional[str] = None,
                       version: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Apply a report from the target device. Reports about someone else's operation, or
        for a finished one, are ignored (``None``)."""
        if status not in ACTIVE_STATUSES + TERMINAL_STATUSES:
            logger.warning("PluginOperations: unknown status {!r} from {}", status, device_id)
            return None

        async def _do(s):
            row = await s.get(PluginOperationModel, op_id)
            if row is None or row.device_id != device_id or row.status in TERMINAL_STATUSES:
                return None
            if status == FAILED:
                code, message, hint = describe_failure(error)
                self._finish(row, FAILED, error_code or code, message, hint, error)
            elif status == SUCCEEDED:
                if version:
                    row.version = version
                self._finish(row, SUCCEEDED, None, None, None, None)
            else:
                if status in _EARLY and row.status not in _EARLY:
                    return None                              # never move backwards
                row.status, row.updated_at = status, _now()
            await s.flush()
            return view_of(row), row.user_id

        out = await self._run(_do)
        if out is None:
            return None
        view, user_id = out
        await self._notify(user_id, view)
        return view

    async def mark_delivered(self, op_id: str) -> None:
        await self._advance(op_id, DELIVERED)

    async def fail(self, op_id: str, *, code: str, message: str, hint: str = "",
                   detail: Optional[str] = None) -> Optional[Dict[str, Any]]:
        async def _do(s):
            row = await s.get(PluginOperationModel, op_id)
            if row is None or row.status in TERMINAL_STATUSES:
                return None
            self._finish(row, FAILED, code, message, hint, detail)
            await s.flush()
            return view_of(row), row.user_id
        out = await self._run(_do)
        if out is None:
            return None
        await self._notify(out[1], out[0])
        return out[0]

    async def _advance(self, op_id: str, status: str) -> None:
        async def _do(s):
            row = await s.get(PluginOperationModel, op_id)
            if row is None or row.status != REQUESTED:
                return None
            row.status, row.updated_at = status, _now()
            await s.flush()
            return view_of(row), row.user_id
        out = await self._run(_do)
        if out is not None:
            await self._notify(out[1], out[0])

    @staticmethod
    def _finish(row: PluginOperationModel, status: str, code: Optional[str], message: Optional[str],
                hint: Optional[str], detail: Optional[str]) -> None:
        now = _now()
        row.status, row.updated_at, row.finished_at = status, now, now
        row.error_code, row.error_message, row.error_hint = code, message, hint
        row.error_detail = (detail or None) and detail[:2000]

    # ------------------------------------------------------------------ push
    async def _notify(self, user_id: str, view: Dict[str, Any]) -> None:
        """Tell the owner's connected devices (they hold an open WebSocket) and the event bus."""
        if self._bus is not None:
            try:
                await self._bus.publish(EventType.PLUGIN_OPERATION.value,
                                        {"user_id": user_id, **view}, source="plugin_operations")
            except Exception as exc:
                logger.debug("PluginOperations: event publish failed: {}", exc)
        try:
            devices = await self._devices.list_devices(user_id)
        except Exception as exc:
            logger.debug("PluginOperations: cannot list devices for push: {}", exc)
            return
        frame = {"type": PUSH_FRAME_TYPE, "operation": view}
        for d in devices:
            try:
                if await self._devices.is_connected(d.id):
                    await self._devices.send(d.id, frame)
            except Exception as exc:                      # one dead socket must not stop the rest
                logger.debug("PluginOperations: push to {} failed: {}", d.id, exc)

    # ------------------------------------------------------------------ watchdog
    def _watch(self, op_id: str, user_id: str) -> None:
        task = asyncio.create_task(self._watchdog(op_id, user_id), name=f"plugin-op:{op_id[:8]}")
        self._watchers[op_id] = task
        task.add_done_callback(lambda _t, k=op_id: self._watchers.pop(k, None))

    async def _watchdog(self, op_id: str, user_id: str) -> None:
        loop = asyncio.get_running_loop()
        offline_since: Optional[float] = None
        try:
            while True:
                await asyncio.sleep(self.TICK_SECONDS)
                view = await self._snapshot(op_id)
                if view is None or view["terminal"]:
                    return
                device_id = view["device_id"]
                now = _now()
                age = (now - _aware(datetime.fromisoformat(view["created_at"]))).total_seconds()
                idle = (now - _aware(datetime.fromisoformat(view["updated_at"]))).total_seconds()

                connected = False
                try:
                    connected = bool(await self._devices.is_connected(device_id))
                except Exception:
                    pass
                if connected:
                    offline_since = None
                elif offline_since is None:
                    offline_since = loop.time()

                if offline_since is not None and loop.time() - offline_since >= self.OFFLINE_GRACE_SECONDS:
                    code, message, hint = describe_failure("device offline")
                    await self.fail(op_id, code=code, message=message, hint=hint,
                                    detail="device disconnected while the operation was running")
                    return
                if age >= self.HARD_LIMIT_SECONDS or idle >= self.STALL_SECONDS:
                    await self.fail(
                        op_id, code="device_unresponsive",
                        message="The device stopped responding before the installation finished.",
                        hint="Open the PAI app on that device and check it is running, then try again.",
                        detail=f"no progress for {int(idle)}s (operation age {int(age)}s)")
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("PluginOperations: watchdog for {} crashed", op_id)

    async def _snapshot(self, op_id: str) -> Optional[Dict[str, Any]]:
        async def _do(s):
            row = await s.get(PluginOperationModel, op_id)
            return view_of(row) if row is not None else None
        return await self._run(_do)
