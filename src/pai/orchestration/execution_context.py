"""
Execution context for a single orchestration request.

The context is intentionally request-scoped. It carries user/session
information through planning, authorization, task creation and execution.

`ContextManager` also owns the session registry that backs the `/sessions`,
`/status`, `/clear`, `/exit`, and `/new` chat commands.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pai.common.manager import Manager

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ExecutionContext:
    user_id: str
    session_id: str

    request_id: str = field(default_factory=lambda: str(uuid4()))

    agent_id: Optional[str] = None
    source_device_id: Optional[str] = None

    goal: Optional[str] = None

    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    user_preferences: Dict[str, Any] = field(default_factory=dict)

    metadata: Dict[str, Any] = field(default_factory=dict)

    created_at: datetime = field(default_factory=utcnow)

    # Device and execution parameters
    device_id: Optional[str] = None
    executor_id: Optional[str] = None
    plugins: Optional[List[str]] = None

    source: Optional[str] = None
    priority: int = 0
    timeout: float = 30.0
    max_retries: int = 3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "source_device_id": self.source_device_id,
            "goal": self.goal,
            "conversation_history": self.conversation_history,
            "user_preferences": self.user_preferences,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "device_id": self.device_id,
            "executor_id": self.executor_id,
            "plugins": self.plugins,
            "source": self.source,
            "priority": self.priority,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }


# ---------------------------------------------------------------------------
# Session record shape
# ---------------------------------------------------------------------------
#
# Every entry in `_sessions` is a plain dict with this shape:
#
#     {
#       "session_id":    str,
#       "user_id":       str,
#       "name":          str,           # "" if unnamed
#       "created_at":    ISO-8601 str,
#       "updated_at":    ISO-8601 str,
#       "ended":         bool,          # set by /exit
#       "message_count": int,           # number of completed turns
#       "history":       List[turn],    # {timestamp, user, assistant}
#     }
#
# Storing dicts (rather than another dataclass) keeps the JSON encoding for
# `/status` and `/sessions` trivial and matches how `record_interaction`
# already shaped history entries.


class ContextManager(Manager):
    """
    Maintains user-scoped preferences and per-session conversation state.

    A *session* is a distinct conversation belonging to a user. Sessions are
    created lazily (via `create_context`, or the first `record_interaction`)
    and are managed through the methods that back the slash commands:

        list_sessions / get_sessions     → /sessions
        get_session_info / get_session   → /status
        clear_session / clear_conversation → /clear
        end_session / close_session      → /exit
        delete_session                   → full removal (unused today but
                                           provided as a destructive fallback)
        create_context(..., new=True)    → /new

    Concurrency: every mutating method takes `self._lock`.
    """
    name = "context_manager"

    def __init__(self, max_history: int = 50):
        super().__init__()

        self.max_history = max_history

        # Per-user preferences (unchanged from the original implementation)
        self._preferences: Dict[str, Dict[str, Any]] = {}

        # session_id → session record (see shape above)
        self._session_registry: Dict[str, Dict[str, Any]] = {}

        # user_id → [session_id, ...] in creation order
        self._user_sessions: Dict[str, List[str]] = {}

        # user_id → most recently used session_id
        self._current_session: Dict[str, str] = {}

        self._active_tasks: Dict[str, Dict[str, Any]] = {}

        self._lock = asyncio.Lock()

    async def on_initialize(self) -> None:
        return None

    async def _on_shutdown(self) -> None:
        async with self._lock:
            self._session_registry.clear()
            self._user_sessions.clear()
            self._current_session.clear()
            self._preferences.clear()
            self._active_tasks.clear()

    # =================================================================
    # Internal helpers — callers must hold self._lock
    # =================================================================

    def _new_session_record(self, user_id: str, name: Optional[str]) -> Dict[str, Any]:
        now = utcnow().isoformat()
        return {
            "session_id": f"session-{uuid4()}",
            "user_id": user_id,
            "name": name or "",
            "created_at": now,
            "updated_at": now,
            "ended": False,
            "message_count": 0,
            "history": [],
        }

    def _ensure_session_locked(
        self,
        user_id: str,
        session_id: Optional[str],
        *,
        name: Optional[str] = None,
    ) -> str:
        """
        Return a session id, creating and registering the session if needed.

        - `session_id is None` → always creates a fresh session.
        - `session_id` exists  → returns it (raises PermissionError if it
                                 belongs to a different user).
        - `session_id` is new  → creates one with that exact id, so a client
                                 that reconnects after a backend restart can
                                 keep using the same id.

        The `_user_sessions` index and `_current_session` pointer are kept
        in sync, so callers never touch `_sessions` directly.
        """
        if session_id is None:
            record = self._new_session_record(user_id, name)
            sid = record["session_id"]
            self._session_registry[sid] = record
            self._user_sessions.setdefault(user_id, []).append(sid)
            self._current_session[user_id] = sid
            return sid

        record = self._session_registry.get(session_id)
        if record is None:
            record = self._new_session_record(user_id, name)
            record["session_id"] = session_id
            self._session_registry[session_id] = record
            self._user_sessions.setdefault(user_id, []).append(session_id)
            self._current_session[user_id] = session_id
            return session_id

        if record["user_id"] != user_id:
            raise PermissionError(
                f"Session {session_id} belongs to another user"
            )
        if name and not record["name"]:
            record["name"] = name
        self._current_session[user_id] = session_id
        return session_id

    def _resolve_session_locked(
        self,
        user_id: str,
        session_id: Optional[str],
    ) -> Optional[str]:
        """
        Resolve a session id for read/write operations.

        - If `session_id` is given and exists for this user → return it.
        - If `session_id` is given but missing → return None (caller decides).
        - If `session_id` is None → fall back to the user's current session.
        """
        if session_id is not None:
            record = self._session_registry.get(session_id)
            if record is None or record["user_id"] != user_id:
                return None
            return session_id
        current = self._current_session.get(user_id)
        if current is None:
            return None
        return current if current in self._session_registry else None

    # =================================================================
    # Public: context creation & interaction recording
    # =================================================================

    async def create_context(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        *,
        agent_id: Optional[str] = None,
        source_device_id: Optional[str] = None,
        goal: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        new: bool = False,
    ) -> ExecutionContext:
        """
        Create or resume an `ExecutionContext`.

        Semantics:
          - `session_id` given         → resume that session (create if
                                         missing so reconnects work).
          - `session_id=None, new=True` → always start a fresh session
                                         (this is what `/new` uses).
          - `session_id=None, new=False` → resume the caller's current
                                         session if one exists, else create
                                         a new one. This keeps chat
                                         continuity for callers that don't
                                         pass a session id.
        """
        async with self._lock:
            name = (metadata or {}).get("name") if metadata else None

            if new and session_id is not None:
                raise ValueError(
                    "create_context: pass either `session_id` or `new=True`, "
                    "not both"
                )

            if new:
                sid = self._ensure_session_locked(user_id, None, name=name)
            elif session_id is not None:
                sid = self._ensure_session_locked(user_id, session_id, name=name)
            else:
                current = self._current_session.get(user_id)
                if current is not None and current in self._session_registry:
                    sid = current
                    if name and not self._session_registry[sid]["name"]:
                        self._session_registry[sid]["name"] = name
                else:
                    sid = self._ensure_session_locked(user_id, None, name=name)

            record = self._session_registry[sid]
            history = list(record["history"])[-self.max_history:]

            return ExecutionContext(
                user_id=user_id,
                session_id=sid,
                agent_id=agent_id,
                source_device_id=source_device_id,
                goal=goal,
                conversation_history=history,
                user_preferences=dict(self._preferences.get(user_id, {})),
                metadata=metadata or {},
            )

    async def record_interaction(
        self,
        user_id: str,
        user_message: str,
        response: Dict[str, Any],
        *,
        session_id: Optional[str] = None,
    ) -> None:
        """
        Append a completed turn to a session's history.

        If `session_id` is omitted, writes to the caller's current session
        (which `create_context` set earlier in the request). If no current
        session exists, a fresh one is created so the interaction is never
        silently dropped.

        The orchestrator should pass `session_id=execution_context.session_id`
        explicitly to avoid the current-session fallback (which is only
        correct when one request per user is in flight).
        """
        async with self._lock:
            if session_id is not None:
                sid = self._ensure_session_locked(user_id, session_id)
            else:
                current = self._current_session.get(user_id)
                if current is not None and current in self._session_registry:
                    sid = current
                else:
                    sid = self._ensure_session_locked(user_id, None)

            record = self._session_registry[sid]
            record["history"].append({
                "timestamp": utcnow().isoformat(),
                "user": user_message,
                "assistant": response,
            })
            record["message_count"] = len(record["history"])
            record["updated_at"] = utcnow().isoformat()

            if len(record["history"]) > self.max_history:
                del record["history"][:-self.max_history]

            self._current_session[user_id] = sid

    async def update_preferences(
        self,
        user_id: str,
        preferences: Dict[str, Any],
    ) -> None:
        async with self._lock:
            self._preferences.setdefault(user_id, {}).update(preferences)

    # =================================================================
    # Session management (backs the slash commands)
    # =================================================================

    async def list_sessions(
        self,
        user_id: str,
        *,
        include_ended: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Return the user's sessions, newest first.

        Each item is a shallow dict (no history) with:
            session_id, name, created_at, updated_at, message_count, ended

        Ended sessions are hidden by default — `/sessions` shows only the
        live ones. Pass `include_ended=True` to see them anyway.
        """
        async with self._lock:
            out: List[Dict[str, Any]] = []
            for sid in reversed(self._user_sessions.get(user_id, [])):
                record = self._session_registry.get(sid)
                if record is None:
                    continue
                if not include_ended and record["ended"]:
                    continue
                out.append({
                    "session_id": record["session_id"],
                    "name": record["name"],
                    "created_at": record["created_at"],
                    "updated_at": record["updated_at"],
                    "message_count": record["message_count"],
                    "ended": record["ended"],
                })
            return out

    async def get_sessions(
        self,
        user_id: str,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Alias for `list_sessions` (kept because `_call_first_available`
        tries both names)."""
        return await self.list_sessions(user_id, **kwargs)

    async def get_session_info(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        *,
        include_history: bool = False,
    ) -> Dict[str, Any]:
        """
        Return metadata for a single session.

        If `session_id` is omitted, uses the caller's current session.
        Raises KeyError if no such session exists, PermissionError if it
        belongs to a different user.
        """
        async with self._lock:
            sid = self._resolve_session_locked(user_id, session_id)
            if sid is None:
                raise KeyError(
                    f"No session found for user {user_id!r}"
                    + (f" (id={session_id})" if session_id else "")
                )
            record = self._session_registry[sid]
            info: Dict[str, Any] = {
                "session_id": record["session_id"],
                "user_id": record["user_id"],
                "name": record["name"],
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
                "message_count": record["message_count"],
                "ended": record["ended"],
            }
            if include_history:
                info["history"] = list(record["history"])
            return info

    async def get_session(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return await self.get_session_info(user_id, session_id, **kwargs)

    async def session_info(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return await self.get_session_info(user_id, session_id, **kwargs)

    async def clear_session(
        self,
        user_id: str,
        session_id: Optional[str] = None,
    ) -> int:
        """
        Clear the session's conversation history.

        Returns the number of turns removed. The session itself stays open
        and remains the caller's current session. Use `delete_session` to
        remove the session entirely.
        """
        async with self._lock:
            sid = self._resolve_session_locked(user_id, session_id)
            if sid is None:
                raise KeyError(
                    f"No session found for user {user_id!r}"
                    + (f" (id={session_id})" if session_id else "")
                )
            record = self._session_registry[sid]
            n = len(record["history"])
            record["history"].clear()
            record["message_count"] = 0
            record["updated_at"] = utcnow().isoformat()
            return n

    async def clear_conversation(
        self,
        user_id: str,
        session_id: Optional[str] = None,
    ) -> int:
        return await self.clear_session(user_id, session_id)

    async def delete_session(
        self,
        user_id: str,
        session_id: Optional[str] = None,
    ) -> bool:
        """
        Remove a session entirely.

        Returns True if the session existed and was removed, False if it
        didn't exist. Raises PermissionError if the session belongs to a
        different user (so this can't be used to probe other users' ids).
        """
        async with self._lock:
            sid = self._resolve_session_locked(user_id, session_id)
            if sid is None:
                return False
            record = self._session_registry.pop(sid)
            if record["user_id"] != user_id:
                self._session_registry[sid] = record          # restore
                raise PermissionError(
                    f"Session {sid} belongs to another user"
                )

            user_list = self._user_sessions.get(user_id, [])
            if sid in user_list:
                user_list.remove(sid)

            if self._current_session.get(user_id) == sid:
                # Fall back to the most recent remaining session, if any
                if user_list:
                    self._current_session[user_id] = user_list[-1]
                else:
                    self._current_session.pop(user_id, None)
            return True

    async def end_session(
        self,
        user_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Mark a session as ended.

        History is preserved but `list_sessions` hides it by default. The
        current-session pointer moves to any other active session the user
        has, or is cleared if none remain.

        Note: we deliberately do **not** reject later `create_context` calls
        that pass an ended `session_id` — resuming a soft-closed session is
        more forgiving than erroring, and `/status` still reports
        `ended: true` so the state is honest.
        """
        async with self._lock:
            sid = self._resolve_session_locked(user_id, session_id)
            if sid is None:
                raise KeyError(
                    f"No session found for user {user_id!r}"
                    + (f" (id={session_id})" if session_id else "")
                )
            record = self._session_registry[sid]
            record["ended"] = True
            record["updated_at"] = utcnow().isoformat()

            if self._current_session.get(user_id) == sid:
                remaining = [
                    s for s in self._user_sessions.get(user_id, [])
                    if s != sid
                    and s in self._session_registry
                    and not self._session_registry[s]["ended"]
                ]
                if remaining:
                    self._current_session[user_id] = remaining[-1]
                else:
                    self._current_session.pop(user_id, None)

            return {
                "session_id": sid,
                "ended": True,
                "ended_at": record["updated_at"],
                "message_count": record["message_count"],
            }

    async def close_session(
        self,
        user_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.end_session(user_id, session_id)

    async def get_current_session_id(self, user_id: str) -> Optional[str]:
        """Convenience accessor for callers that need to know which session
        a user's next message would land in."""
        async with self._lock:
            sid = self._current_session.get(user_id)
            return sid if sid in self._session_registry else None

    # =================================================================
    # Active task tracking (unchanged)
    # =================================================================

    async def register_active_task(
        self,
        task_id: str,
        context: ExecutionContext,
    ) -> None:
        async with self._lock:
            self._active_tasks[task_id] = {
                "task_id": task_id,
                "request_id": context.request_id,
                "user_id": context.user_id,
                "session_id": context.session_id,
                "started_at": utcnow().isoformat(),
            }

    async def complete_task(self, task_id: str) -> None:
        async with self._lock:
            self._active_tasks.pop(task_id, None)

    async def get_active_tasks(self) -> Dict[str, Dict[str, Any]]:
        async with self._lock:
            return dict(self._active_tasks)

