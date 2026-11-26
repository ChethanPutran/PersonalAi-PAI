"""Small helpers shared by the CLI commands (output, error text, operation following)."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, Tuple

import httpx

from .config import DeviceConfig
from .transport import APIClient

@dataclass
class Ctx:
    """What a command needs: the logged-in client, THIS device's id (or ``--device``) and the config."""
    client: APIClient
    device_id: Optional[str]
    config: DeviceConfig


TERMINAL_TASK_STATUSES = ("completed", "failed", "cancelled", "timeout", "expired")

STAGE_TEXT = {
    "requested": "request received",
    "delivered": "sent to the device",
    "downloading": "downloading",
    "verifying": "verifying",
    "installing": "installing",
    "awaiting_permission": "waiting for you to allow a permission on the device",
}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def print_table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def dim(text: str = "", *, end: str = "\n") -> None:
    """Secondary information (status lines, hints) on stderr, dimmed when it is a terminal."""
    if sys.stderr.isatty():
        text = f"\033[2m{text}\033[0m"
    print(text, file=sys.stderr, end=end, flush=True)


async def read_line(prompt: str = "") -> str:
    """``input()`` without blocking the event loop AND without keeping the process alive.

    ``asyncio.to_thread(input)`` leaves a non-daemon executor thread stuck on stdin, so after
    Ctrl+C the process hangs until Enter is pressed. A daemon thread is simply abandoned at exit.
    Raises ``EOFError`` on end of input; keeps ``input()``'s line editing and history.
    """
    loop = asyncio.get_running_loop()
    fut: "asyncio.Future[Optional[str]]" = loop.create_future()

    def _set(value: Optional[str]) -> None:
        if not fut.done():
            fut.set_result(value)

    def work() -> None:
        try:
            line: Optional[str] = input(prompt)
        except EOFError:
            line = None
        except BaseException:                      # noqa: BLE001 - never lose the waiting coroutine
            line = None
        loop.call_soon_threadsafe(_set, line)

    threading.Thread(target=work, name="pai-stdin", daemon=True).start()
    line = await fut
    if line is None:
        raise EOFError
    return line


def yes_no(value: Any) -> str:
    return "on" if value else "off"


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

def parse_on_off(text: Optional[str]) -> Optional[bool]:
    """``on|off|yes|no|true|false|1|0`` -> bool; ``None`` stays ``None`` (leave unchanged)."""
    if text is None:
        return None
    t = text.strip().lower()
    if t in ("on", "yes", "true", "1", "enable", "enabled"):
        return True
    if t in ("off", "no", "false", "0", "disable", "disabled"):
        return False
    raise ValueError(f"expected on or off, got {text!r}")


def parse_params(pairs: Optional[Sequence[str]], json_text: Optional[str]) -> Dict[str, Any]:
    """``--json-params '{...}'`` (or ``@file``) first, then ``--param key=value`` on top.
    A value that parses as JSON (``3``, ``true``, ``[1,2]``) is typed, anything else is a string."""
    params: Dict[str, Any] = {}
    if json_text:
        if json_text.startswith("@"):
            with open(json_text[1:], encoding="utf-8") as fh:
                json_text = fh.read()
        loaded = json.loads(json_text)
        if not isinstance(loaded, dict):
            raise ValueError("--json-params must be a JSON object")
        params.update(loaded)
    for item in pairs or []:
        key, sep, raw = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--param expects key=value, got {item!r}")
        try:
            params[key.strip()] = json.loads(raw)
        except ValueError:
            params[key.strip()] = raw
    return params


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def error_text(exc: httpx.HTTPStatusError) -> Tuple[str, Optional[str]]:
    """The server's own words for an HTTP error: ``(message, code)``.

    Understands ``{"detail": "text"}``, ``{"detail": {"code", "message"}}`` and FastAPI's
    validation list, so a 403 "remote install is turned off on 'Work PC'" reads as such."""
    try:
        body = exc.response.json()
    except Exception:
        return (exc.response.text or f"HTTP {exc.response.status_code}").strip()[:300], None
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error") or detail)[:300], detail.get("code")
    if isinstance(detail, list):
        msgs = [str(d.get("msg", d)).replace("Value error, ", "") for d in detail if isinstance(d, dict)]
        return "; ".join(msgs)[:300] or str(detail)[:300], None
    return str(detail)[:300], None


# ---------------------------------------------------------------------------
# Following a remote install / uninstall operation
# ---------------------------------------------------------------------------

async def follow_operation(
    client: APIClient,
    operation_id: str,
    *,
    label: str,
    timeout: float = 600.0,
    interval: float = 0.5,
    max_interval: float = 3.0,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    out: Callable[[str], None] = print,
) -> int:
    """Watch ``GET /plugins/operations/{id}`` until it finishes; print each stage change.

    A CLI has no device WebSocket to be pushed to, so it polls, with a backoff capped at
    ``max_interval``. Returns 0 on success, 1 on failure/timeout. On failure it prints the
    server's plain message and hint (never raw device text).
    """
    start = clock()
    last_status: Optional[str] = None
    wait = interval
    net_errors = 0
    while True:
        try:
            op = await client.get_operation(operation_id)
            net_errors = 0
        except httpx.HTTPStatusError as exc:
            message, _ = error_text(exc)
            out(f"✗ can't read the status of this operation: {message}")
            return 1
        except httpx.TransportError as exc:
            net_errors += 1
            if net_errors >= 4:
                out(f"✗ lost contact with the backend: {exc}")
                return 1
            op = None
        if op is not None:
            status = op.get("status")
            elapsed = clock() - start
            if status != last_status:
                last_status = status
                if status == "succeeded":
                    out(f"✓ {label} done ({elapsed:.1f}s)")
                    return 0
                if status == "failed":
                    err = op.get("error") or {}
                    out(f"✗ {label} failed: {err.get('message') or 'the device could not finish it'}")
                    if err.get("hint"):
                        out(f"  {err['hint']}")
                    if err.get("code"):
                        dim(f"  ({err['code']})")
                    return 1
                out(f"… {label}: {STAGE_TEXT.get(status, status)} ({elapsed:.1f}s)")
        if clock() - start >= timeout:
            out(f"✗ {label} is still not finished after {timeout:.0f}s. It keeps going on the server.")
            out(f"  Check it later:  pai-device operations --active   (operation {operation_id})")
            return 1
        await sleep(wait)
        wait = min(wait * 1.5, max_interval)


# ---------------------------------------------------------------------------
# Tasks and confirmations
# ---------------------------------------------------------------------------

def print_confirmation(info: Dict[str, Any], *, task_id: Optional[str] = None, out: Callable[[str], None] = print) -> None:
    """Show what the person is being asked to approve (the server already masked secrets)."""
    out(f"⚠ Approval needed: {info.get('capability', '?')}"
        + (f"  (risk: {info['risk']})" if info.get("risk") else ""))
    if info.get("reason"):
        out(f"  why: {info['reason']}")
    if info.get("device_id"):
        out(f"  device: {info['device_id']}")
    for p in info.get("parameters_summary") or []:
        out(f"  {p.get('name')}: {p.get('value_preview')}")
    scopes = info.get("allowed_scopes") or ["once"]
    out(f"  you can approve: {', '.join(str(x) for x in scopes)}")
    if task_id:
        out(f"  approve:  pai-device approve {task_id}      deny:  pai-device deny {task_id}")


async def wait_for_task(
    client: APIClient, task_id: str, *, timeout: float = 120.0, interval: float = 0.4,
    max_interval: float = 2.0,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Dict[str, Any]:
    """Poll ``GET /tasks/{id}`` until it ends (or ``timeout``); returns the last task dict."""
    start, wait = clock(), interval
    task: Dict[str, Any] = {}
    while True:
        task = (await client.get_task(task_id)).get("task") or {}
        if task.get("status") in TERMINAL_TASK_STATUSES or clock() - start >= timeout:
            return task
        await sleep(wait)
        wait = min(wait * 1.5, max_interval)


def print_task_outcome(task: Dict[str, Any], *, as_json: bool = False) -> int:
    """Print a finished task's result or error; return the exit code."""
    status = task.get("status")
    if as_json:
        print_json(task)
        return 0 if status == "completed" else 1
    result = task.get("result") or {}
    if status == "completed" and result.get("success", True) is not False:
        output = result.get("output") if isinstance(result, dict) else result
        print_json(output if output is not None else result)
        return 0
    info = task.get("error_info") or {}
    message = info.get("message") or task.get("error") or (result.get("error") if isinstance(result, dict) else None)
    print(f"✗ {message or f'task {status}'}", file=sys.stderr)
    if info.get("hint"):
        dim(f"  {info['hint']}")
    if status not in TERMINAL_TASK_STATUSES:
        dim(f"  (still {status}; check later: pai-device approve/deny, or the Tasks screen)")
    return 1
