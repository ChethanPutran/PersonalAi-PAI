"""Who may do what to a device from OUTSIDE it.

A user can have several devices (say A and B). Acting on B from anywhere else (from A, from the
web, from a schedule, from an agent) is *remote* and needs the owner's explicit opt-in on B:

    allow_remote_install   install / uninstall plugins on B
    allow_remote_invoke    run plugin capabilities on B

Both default to off. An action that comes FROM the target device itself is local and always
allowed. The check is fail-closed (unknown device, someone else's device => denied) and lives
here so every path (plugin routes, the tester, chat plans, schedules, agents) asks the same
question. The device re-checks on its own side before acting; this server check is not the only
gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

RemoteKind = Literal["install", "invoke"]

DEVICE_NOT_FOUND = "device_not_found"
REMOTE_INSTALL_DISABLED = "remote_install_disabled"
REMOTE_INVOKE_DISABLED = "remote_invoke_disabled"


@dataclass(frozen=True)
class RemoteAccessDenial:
    code: str
    message: str


async def remote_access_denial(
    device_manager: Any,
    *,
    user_id: str,
    target_device_id: Optional[str],
    source_device_id: Optional[str],
    kind: RemoteKind,
) -> Optional[RemoteAccessDenial]:
    """``None`` when allowed, else why not. ``source_device_id`` is the device the request
    came from (None for the web, a schedule, an agent...)."""
    device = await device_manager.get(target_device_id) if target_device_id else None
    if device is None or getattr(device, "user_id", None) != user_id:
        # Same answer for "missing" and "not yours": never confirm another user's device exists.
        return RemoteAccessDenial(DEVICE_NOT_FOUND, "Device not found.")

    if source_device_id and source_device_id == target_device_id:
        return None                                   # the device acting on itself: local

    name = getattr(device, "name", "") or "that device"
    if kind == "install":
        if not getattr(device, "allow_remote_install", False):
            return RemoteAccessDenial(
                REMOTE_INSTALL_DISABLED,
                f"Remote plugin installation is turned off on '{name}'. "
                f"Turn on \"Allow remote plugin install\" in the PAI app on that device.")
    elif not getattr(device, "allow_remote_invoke", False):
        return RemoteAccessDenial(
            REMOTE_INVOKE_DISABLED,
            f"Remote plugin use is turned off on '{name}'. "
            f"Turn on \"Allow remote plugin use\" in the PAI app on that device.")
    return None
