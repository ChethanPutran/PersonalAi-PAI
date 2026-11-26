"""``pai-device chat``: talk to the assistant by text, streamed token by token.

One-shot (``chat "what's on my screen?"``, or text piped on stdin) or an interactive REPL
(``chat``) with ``/new``, ``/status``, ``/help``, ``/quit``. Uses ``POST /chat/stream`` (SSE:
``status``, ``delta``, ``done``, ``error``) and, alongside it, the session's task events
(``GET /tasks/{session}/events``) so an action that needs your approval asks you right there
instead of waiting silently. Nothing is ever approved without an explicit answer.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO

import httpx

from .cli_support import Ctx, dim, error_text, print_confirmation, print_json, read_line

CHAT_CAPABILITY = "chat.reply"
HELP_TEXT = "/new  start a new conversation   /status  show the session   /help   /quit"


@dataclass
class TurnResult:
    status: str = "unknown"
    message: str = ""
    session_id: str = ""
    error: Optional[str] = None
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    streamed: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.status == "completed"


# ---------------------------------------------------------------------------
# Session id kept between runs
# ---------------------------------------------------------------------------

def _session_file(ctx: Ctx) -> Path:
    return Path(ctx.config.state_dir) / "chat_session"


def load_session(ctx: Ctx) -> Optional[str]:
    try:
        return _session_file(ctx).read_text().strip() or None
    except OSError:
        return None


def save_session(ctx: Ctx, session_id: str) -> None:
    try:
        f = _session_file(ctx)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(session_id)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Confirmations raised while a turn runs
# ---------------------------------------------------------------------------

ConfirmHandler = Callable[[Dict[str, Any]], Any]


async def ask_in_terminal(ctx: Ctx, task: Dict[str, Any]) -> None:
    """Show what needs approval and ask (on a terminal). Without a terminal: say how to approve."""
    task_id = task.get("id") or task.get("task_id")
    info = task.get("confirmation") or {"capability": task.get("capability")}
    print_confirmation(info, task_id=None if sys.stdin.isatty() else task_id, out=dim)
    if not sys.stdin.isatty() or not task_id:
        return
    scopes = [str(s) for s in (info.get("allowed_scopes") or ["once"])]
    options = "[y] yes once" + ("  [s] this session" if "session" in scopes else "") + "  [n] no"
    answer = (await read_line(f"  Approve? {options}: ")).strip().lower()
    try:
        if answer in ("y", "yes"):
            await ctx.client.confirm_task(task_id, scope="once")
        elif answer in ("s", "session") and "session" in scopes:
            await ctx.client.confirm_task(task_id, scope="session")
        else:
            await ctx.client.deny_task(task_id)
            dim("  denied")
    except httpx.HTTPStatusError as exc:
        dim(f"  ✗ {error_text(exc)[0]}")


async def watch_confirmations(ctx: Ctx, session_id: str, on_confirm: Callable[[Dict[str, Any]], Any]) -> None:
    """Follow the session's task events; call ``on_confirm(task)`` for each approval request.
    Failure to follow is not fatal (the reply still streams); it is just reported once."""
    try:
        async for event, data in ctx.client.sse("GET", f"/tasks/{session_id}/events"):
            if event == "task.awaiting_confirmation" and isinstance(data, dict):
                await on_confirm(data)
    except asyncio.CancelledError:
        raise
    except httpx.HTTPError as exc:
        dim(f"(can't follow approvals for this session: {exc})")


# ---------------------------------------------------------------------------
# One turn
# ---------------------------------------------------------------------------

async def run_turn(
    ctx: Ctx, device_id: str, session_id: str, message: str, *,
    show_status: bool = True, stream_text: bool = True, out: Optional[TextIO] = None,
    on_confirm: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> TurnResult:
    """Send one message and print the reply as it streams. ``stream_text=False`` collects only."""
    out = out or sys.stdout
    result = TurnResult(session_id=session_id)
    body = {"device_id": device_id, "message": message, "mode": "text", "session_id": session_id}
    watcher = None
    mid_line = False                      # reply text written without its final newline yet
    if on_confirm is not None:
        watcher = asyncio.create_task(watch_confirmations(ctx, session_id, on_confirm), name="chat.approvals")
    try:
        async for event, data in ctx.client.sse("POST", "/chat/stream", json=body):
            if event == "delta":
                piece = (data or {}).get("delta", "") if isinstance(data, dict) else ""
                if piece:
                    result.streamed = True
                    if stream_text:
                        out.write(piece)
                        out.flush()
                        mid_line = not piece.endswith("\n")
            elif event == "status":
                if show_status and isinstance(data, dict) and data.get("message"):
                    if mid_line:                       # never cut a sentence with a progress line
                        out.write("\n")
                        out.flush()
                        mid_line = False
                    dim(f"· {data['message']}")
            elif event == "done" and isinstance(data, dict):
                result.status = data.get("status", "unknown")
                result.message = data.get("message", "")
                result.session_id = data.get("session_id") or session_id
                result.tasks = [t for t in data.get("tasks") or [] if t.get("capability") != CHAT_CAPABILITY]
            elif event == "error":
                result.error = (data or {}).get("message", "request failed") if isinstance(data, dict) else str(data)
    except httpx.HTTPStatusError as exc:
        result.error = error_text(exc)[0]
    except httpx.TransportError as exc:
        result.error = f"can't reach the backend: {exc}"
    finally:
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watcher
    if stream_text:
        if not result.streamed and result.message and result.error is None:
            out.write(result.message)            # nothing streamed (a command, or a failure message)
        if mid_line or (not result.streamed and result.message):
            out.write("\n")
            out.flush()
    return result


def _report(result: TurnResult) -> None:
    for t in result.tasks:
        mark = "✓" if t.get("status") == "completed" else "✗"
        line = f"  {mark} {t.get('capability')}"
        if t.get("device_id"):
            line += f" on {t['device_id'][:8]}"
        if t.get("error"):
            line += f": {t['error']}"
        dim(line)
    if result.error:
        print(f"✗ {result.error}", file=sys.stderr)
    elif result.status not in ("completed", "unknown"):
        dim(f"(status: {result.status})")


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

async def _interruptible(coro) -> Any:
    """Run ``coro``; Ctrl+C stops it (closing the stream stops the request server-side) and returns None."""
    task = asyncio.ensure_future(coro)
    loop = asyncio.get_running_loop()
    installed = False
    try:
        loop.add_signal_handler(signal.SIGINT, task.cancel)
        installed = True
    except (NotImplementedError, RuntimeError, ValueError):
        pass
    try:
        return await task
    except asyncio.CancelledError:
        dim("\n(stopped)")
        return None
    finally:
        if installed:
            with contextlib.suppress(Exception):
                loop.remove_signal_handler(signal.SIGINT)


async def cmd_chat(args, ctx: Ctx) -> int:
    device_id = args.device or ctx.device_id
    if not device_id:
        print("error: no device id: run `pai-device run` once so this device registers, or pass --device",
              file=sys.stderr)
        return 1

    session_id = args.session or (None if args.new else load_session(ctx)) or uuid.uuid4().hex
    message = " ".join(args.message).strip()
    if not message and not sys.stdin.isatty():
        message = sys.stdin.read().strip()

    async def on_confirm(task: Dict[str, Any]) -> None:
        await ask_in_terminal(ctx, task)

    if message:                                              # one-shot
        result = await _interruptible(run_turn(
            ctx, device_id, session_id, message, show_status=not args.quiet,
            stream_text=not args.json, on_confirm=on_confirm))
        if result is None:
            return 130
        save_session(ctx, result.session_id or session_id)
        if args.json:
            print_json({"status": result.status, "message": result.message, "session_id": result.session_id,
                        "error": result.error, "tasks": result.tasks})
        else:
            _report(result)
            dim(f"session {result.session_id or session_id}  (continue it by running chat again; --new starts fresh)")
        return 0 if result.ok else 1

    return await repl(args, ctx, device_id, session_id, on_confirm)


async def repl(args, ctx: Ctx, device_id: str, session_id: str, on_confirm) -> int:
    with contextlib.suppress(ImportError):
        import readline  # noqa: F401  (line editing + history for input())
    dim(f"Talking to {ctx.config.api_base} as {ctx.config.email}. {HELP_TEXT}")
    while True:
        try:
            line = (await read_line("you> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            return 0
        if line == "/help":
            dim(HELP_TEXT)
            continue
        if line == "/new":
            session_id = uuid.uuid4().hex
            dim("new conversation")
            continue
        if line == "/status":
            dim(f"session {session_id}\ndevice  {device_id}\nbackend {ctx.config.api_base}")
            continue
        if line.startswith("/"):
            dim(f"unknown command {line.split()[0]!r}. {HELP_TEXT}")
            continue
        print("pai> ", end="", flush=True)
        result = await _interruptible(run_turn(
            ctx, device_id, session_id, line, show_status=not args.quiet,
            stream_text=True, on_confirm=on_confirm))
        if result is None:
            continue
        session_id = result.session_id or session_id
        save_session(ctx, session_id)
        _report(result)


def register(sub: "argparse._SubParsersAction", common: argparse.ArgumentParser) -> None:
    sp = sub.add_parser(
        "chat", parents=[common],
        help="talk to the assistant by text (one message, piped text, or an interactive session)",
        description="Without a message this opens an interactive session (/new /status /quit). The reply "
                    "streams as it is written. Actions that need approval ask you right here.")
    sp.add_argument("message", nargs="*", help="what to say (omit for an interactive session)")
    sp.add_argument("--session", help="continue this conversation id")
    sp.add_argument("--new", action="store_true", help="start a new conversation (default: continue the last one)")
    sp.add_argument("--quiet", "-q", action="store_true", help="hide progress lines like 'Understanding your request…'")


COMMANDS = {"chat": cmd_chat}
