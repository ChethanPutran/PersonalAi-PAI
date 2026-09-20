"""
Execution context for a single orchestration request.

The context is intentionally request-scoped. It carries user/session
information through planning, authorization, task creation and execution.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


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
        }


class ContextManager:
    """
    Maintains lightweight global/session state.

    Individual executions use ExecutionContext objects so concurrent
    requests do not overwrite one another.
    """

    def __init__(self, max_history: int = 50):
        self.max_history = max_history

        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._preferences: Dict[str, Dict[str, Any]] = {}

        self._active_tasks: Dict[str, Dict[str, Any]] = {}

        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def create_context(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        *,
        agent_id: Optional[str] = None,
        source_device_id: Optional[str] = None,
        goal: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionContext:

        async with self._lock:
            session_id = session_id or f"session-{uuid4()}"

            return ExecutionContext(
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
                source_device_id=source_device_id,
                goal=goal,
                conversation_history=list(
                    self._history.get(user_id, [])
                )[-self.max_history:],
                user_preferences=dict(
                    self._preferences.get(user_id, {})
                ),
                metadata=metadata or {},
            )

    async def record_interaction(
        self,
        user_id: str,
        user_message: str,
        response: Dict[str, Any],
    ) -> None:

        async with self._lock:
            history = self._history.setdefault(user_id, [])

            history.append({
                "timestamp": utcnow().isoformat(),
                "user": user_message,
                "assistant": response,
            })

            if len(history) > self.max_history:
                del history[:-self.max_history]

    async def update_preferences(
        self,
        user_id: str,
        preferences: Dict[str, Any],
    ) -> None:

        async with self._lock:
            self._preferences.setdefault(user_id, {}).update(
                preferences
            )

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

    async def shutdown(self) -> None:
        async with self._lock:
            self._history.clear()
            self._preferences.clear()
            self._active_tasks.clear()