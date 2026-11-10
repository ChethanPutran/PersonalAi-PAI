"""
Built-in chat commands.

A command is text that starts with `/` and whose first token is a known
command name. Commands bypass the planner and the LLM entirely — they run
directly against the orchestrator's services and return a response in the
same shape as a normal plan execution, so the HTTP route and the frontend
need no special handling.

To add a new command:

    class FooCommand(ChatCommand):
        name = "foo"
        aliases = ["f"]
        description = "Does the foo thing"
        usage = "/foo [arg]"

        async def execute(self, args, *, context, orchestrator):
            return CommandResult(message="foo done")

    default_command_registry().register(FooCommand())
"""
from __future__ import annotations

import asyncio
import re
import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    status: str = "completed"          # "completed" | "failed"
    message: str = ""                  # user-facing reply
    data: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class ChatCommand(ABC):
    name: str = ""
    aliases: List[str] = []
    description: str = ""
    usage: str = ""

    @abstractmethod
    async def execute(
        self,
        args: List[str],
        *,
        context: Any,                  # ExecutionContext
        orchestrator: Any,             # TaskOrchestrator
    ) -> CommandResult:
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# A command name is a letter/? followed by letters, digits, _ or -.
# This deliberately rejects `/tmp/file.txt` and `/usr/bin/env` so goals
# that happen to mention a Unix path aren't mistaken for commands.
_NAME_RE = re.compile(r"^[a-zA-Z?][a-zA-Z0-9_-]*$")


class CommandRegistry:
    def __init__(self) -> None:
        self._by_name: Dict[str, ChatCommand] = {}

    def register(self, cmd: ChatCommand) -> None:
        for key in [cmd.name, *cmd.aliases]:
            self._by_name[key.lower()] = cmd

    def is_command(self, text: str) -> bool:
        """True iff `text` looks like `/something` with a valid name."""
        s = text.strip()
        if not s.startswith("/"):
            return False
        try:
            first = shlex.split(s[1:])[0]
        except (ValueError, IndexError):
            return False
        return bool(_NAME_RE.match(first))

    def resolve(self, text: str) -> Optional[Tuple[ChatCommand, List[str]]]:
        """
        Return (command, args) if `text` is a registered command.
        Return None if it's not a command OR the name is unknown.
        Use [is_command] to distinguish the two.
        """
        if not self.is_command(text):
            return None
        try:
            parts = shlex.split(text.strip()[1:])
        except ValueError:
            parts = text.strip()[1:].split()
        if not parts:
            return None
        cmd = self._by_name.get(parts[0].lower())
        if cmd is None:
            return None
        return cmd, parts[1:]

    def list_commands(self) -> List[ChatCommand]:
        seen: set = set()
        out: List[ChatCommand] = []
        for cmd in self._by_name.values():
            if id(cmd) in seen:
                continue
            seen.add(id(cmd))
            out.append(cmd)
        return sorted(out, key=lambda c: c.name)


# ---------------------------------------------------------------------------
# Utility: call the first method that exists on an object
# ---------------------------------------------------------------------------

async def _call_first_available(
    obj: Any,
    method_names: List[str],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """
    Try each name in order; call the first one that exists.

    Different ContextManager implementations may name their session
    methods slightly differently (`clear_session` vs `delete_session`,
    etc.). This helper lets a command work with any of them, and raises
    a clear error if none exist.
    """
    for name in method_names:
        method = getattr(obj, name, None)
        if callable(method):
            result = method(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result
    raise AttributeError(
        f"{type(obj).__name__} exposes none of {method_names}. "
        f"Add one of them to support this command."
    )


# ---------------------------------------------------------------------------
# Built-in commands
# ---------------------------------------------------------------------------

class HelpCommand(ChatCommand):
    name = "help"
    aliases = ["h", "?"]
    description = "List available commands"

    async def execute(self, args, *, context, orchestrator):
        registry = orchestrator.command_registry
        lines = ["Available commands:"]
        for cmd in registry.list_commands():
            alias_str = ""
            if cmd.aliases:
                alias_str = " (" + ", ".join(f"/{a}" for a in cmd.aliases) + ")"
            lines.append(f"  /{cmd.name}{alias_str} — {cmd.description}")
            if cmd.usage:
                lines.append(f"      usage: {cmd.usage}")
        return CommandResult(message="\n".join(lines))


class ClearCommand(ChatCommand):
    name = "clear"
    aliases = ["c"]
    description = "Clear the current session's conversation history"

    async def execute(self, args, *, context, orchestrator):
        await _call_first_available(
            orchestrator.context_manager,
            ["clear_session", "clear_conversation", "delete_session"],
            user_id=context.user_id,
            session_id=context.session_id,
        )
        return CommandResult(
            message=f"Conversation history cleared for session "
                    f"{context.session_id}.",
            data={"session_id": context.session_id, "cleared": True},
        )


class NewSessionCommand(ChatCommand):
    name = "new"
    aliases = ["n"]
    description = "Start a new session (optionally with a name)"
    usage = "/new [name]"

    async def execute(self, args, *, context, orchestrator):
        name = " ".join(args).strip() or None

        # `create_context` with `session_id=None` allocates a new session.
        new_ctx = await orchestrator.context_manager.create_context(
            user_id=context.user_id,
            session_id=None,
            new=True,
            source_device_id=context.source_device_id,
            goal="(new session)",
            metadata={"name": name} if name else {},
        )

        label = f" ({name})" if name else ""
        return CommandResult(
            message=(
                f"Started new session: {new_ctx.session_id}{label}\n"
                f"Send the next message with session_id={new_ctx.session_id} "
                f"to use this session."
            ),
            data={"session_id": new_ctx.session_id, "name": name},
        )


class ExitCommand(ChatCommand):
    name = "exit"
    aliases = ["quit", "q", "end"]
    description = "End the current session"

    async def execute(self, args, *, context, orchestrator):
        await _call_first_available(
            orchestrator.context_manager,
            ["end_session", "close_session"],
            user_id=context.user_id,
            session_id=context.session_id,
        )
        return CommandResult(
            message=f"Session {context.session_id} ended.",
            data={"session_id": context.session_id, "ended": True},
        )


class StatusCommand(ChatCommand):
    name = "status"
    description = "Show info about the current session"

    async def execute(self, args, *, context, orchestrator):
        info = await _call_first_available(
            orchestrator.context_manager,
            ["get_session_info", "get_session", "session_info"],
            user_id=context.user_id,
            session_id=context.session_id,
        )
        if not isinstance(info, dict):
            info = {"raw": info}

        lines = [
            f"Session : {context.session_id}",
            f"User    : {context.user_id}",
            f"Device  : {context.source_device_id or '(none)'}",
        ]
        for key in ("name", "message_count", "created_at", "updated_at"):
            if key in info and info[key] not in (None, ""):
                lines.append(f"{key.replace('_', ' ').title():<8}: {info[key]}")

        return CommandResult(message="\n".join(lines), data=info)


class SessionsCommand(ChatCommand):
    name = "sessions"
    aliases = ["ls"]
    description = "List your sessions"

    async def execute(self, args, *, context, orchestrator):
        sessions = await _call_first_available(
            orchestrator.context_manager,
            ["list_sessions", "get_sessions"],
            user_id=context.user_id,
        )
        if not sessions:
            return CommandResult(
                message="No sessions yet.",
                data={"sessions": []},
            )

        lines = ["Sessions:"]
        for s in sessions:
            if isinstance(s, dict):
                sid = s.get("session_id") or s.get("id") or "?"
                name = s.get("name") or "(unnamed)"
            else:
                sid, name = str(s), "(unnamed)"
            marker = " *" if str(sid) == context.session_id else ""
            lines.append(f"  {sid}{marker}  {name}")

        return CommandResult(
            message="\n".join(lines),
            data={"sessions": [
                dict(s) if isinstance(s, dict) else {"session_id": str(s)}
                for s in sessions
            ]},
        )


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------

def default_command_registry() -> CommandRegistry:
    reg = CommandRegistry()
    reg.register(HelpCommand())
    reg.register(ClearCommand())
    reg.register(NewSessionCommand())
    reg.register(ExitCommand())
    reg.register(StatusCommand())
    reg.register(SessionsCommand())
    return reg