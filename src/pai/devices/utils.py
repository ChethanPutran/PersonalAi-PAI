from datetime import datetime, timezone
from typing import Any, Dict

from pai.devices.models import DeviceStatus

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_status(raw) -> DeviceStatus:
    """
    Coerce anything status-like to a valid `DeviceStatus` value string.

    Handles:
        - None                     → "unknown"
        - DeviceStatus.ONLINE      → "online"
        - "DeviceStatus.ONLINE"    → "online"   (enum repr leaked into DB)
        - "online" / "ONLINE"      → "online"

    Single entry point for all status strings — no path can put a bad
    value into the DB or the registry without going through here first.
    """
    if raw is None:
        return DeviceStatus.UNKNOWN
    if isinstance(raw, DeviceStatus):
        return raw
    s = str(raw)
    if s.startswith("DeviceStatus."):
        s = s.split(".", 1)[1].lower()
    s = s.lower()
    if s not in {st.value for st in DeviceStatus}:
        return DeviceStatus.UNKNOWN
    return DeviceStatus(s)


def _device_summary(device, *, connected: bool = False) -> Dict[str, Any]:
    """
    Compact, serializable view of a DeviceInfo for trace attributes.

    Deliberately small — trace attributes are stored per event and
    rendered in the admin UI. A verbose per-device blob makes traces
    unreadable for users with many devices.
    """
    last_seen = getattr(device, "last_seen", None)
    return {
        "id":           getattr(device, "id", None),
        "name":         getattr(device, "name", "") or "",
        "type":         getattr(device, "device_type", "unknown"),
        "platform":     getattr(device, "platform", "unknown"),
        "architecture": getattr(device, "architecture", ""),
        "status":       _normalize_status(getattr(device, "status", None)),
        "user_id":      getattr(device, "user_id", None),
        "connected":    connected,
        "last_seen":    last_seen.isoformat() if last_seen else None,
        "capabilities": list(getattr(device, "capabilities", []) or []),
    }