"""Native OS notifications — platform dispatch."""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


def _notify_linux(title: str, body: str, urgency: str = "normal") -> bool:
    if not shutil.which("notify-send"):
        logger.warning(
            "notify-send not on PATH — install libnotify-bin "
            "(Debian/Ubuntu) or libnotify (Arch/Fedora)"
        )
        return False
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-a", "PAI",
             "-i", "dialog-information", title, body],
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning("notify-send failed: %s", exc)
        return False


def _notify_macos(title: str, body: str, urgency: str = "normal") -> bool:
    # osascript is always present on macOS.
    script = f'display notification "{_esc(body)}" with title "{_esc(title)}"'
    try:
        subprocess.run(
            ["osascript", "-e", script], check=True, timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning("osascript failed: %s", exc)
        return False


def _notify_windows(title: str, body: str, urgency: str = "normal") -> bool:
    try:
        from win10toast import ToastNotifier  # pip install win10toast
    except ImportError:
        logger.warning("win10toast not installed — pip install win10toast")
        return False
    try:
        ToastNotifier().show_toast(
            title, body, duration=10, threaded=True,
        )
        return True
    except Exception as exc:
        logger.warning("win10toast failed: %s", exc)
        return False


def _esc(s: str) -> str:
    """Escape for embedding in an AppleScript string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def show_notification(
    title: str,
    body: str,
    urgency: str = "normal",
) -> bool:
    """Display a notification. Returns True if the OS accepted it."""
    if sys.platform.startswith("linux"):
        return _notify_linux(title, body, urgency)
    if sys.platform == "darwin":
        return _notify_macos(title, body, urgency)
    if sys.platform == "win32":
        return _notify_windows(title, body, urgency)
    logger.warning("Notifications unsupported on platform=%s", sys.platform)
    return False