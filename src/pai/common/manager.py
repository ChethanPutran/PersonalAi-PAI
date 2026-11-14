"""
Base class for all PAI managers.

Provides:
  - idempotent lifecycle: initialize() / shutdown()
  - lazy init: _ensure_initialized()
  - optional session handling: session() / _with_session()
  - default status: get_status()

Subclasses override `_on_initialize` / `_on_shutdown` (NOT `initialize` /
`shutdown`) and get the guards and logging for free. If a subclass needs the
raw public hooks — e.g. to add tracing around the whole thing — it should
override `initialize` and call `await super().initialize()` first/last. The
simpler pattern is almost always the right one.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from pai.core.lifecycle import Lifecycle
from pai.event_bus.bus import EventBus
from pai.session.manager import SessionManager


T = TypeVar("T")


class Manager(Lifecycle):
    """Base for all orchestrator-owned components.

    Every subclass gets two event-publishing helpers so none of them
    reimplement the plumbing:

      • `_publish(event_type, data)` — awaited. Use when the caller
        needs the event to be *in the queue* before continuing (e.g.
        before a state change that the consumer might race).

      • `_emit(event_type, data)` — fire-and-forget. Use for status
        and observability. Spawns a task with a 100ms timeout so a
        full queue can never stall the component's hot path.

    Neither helper raises if the bus is missing or the queue is full —
    event publication is always best-effort. If you need delivery
    guarantees, that's a signal you want a DB write, not an event.
    """

    #: Human-readable identifier, used only for logging and status.
    #: Subclasses should override this — e.g. `name = "plugin_manager"`.
    name: str = "manager"

    def __init__(
        self,
        *,
        event_bus: Optional[EventBus] = None,
        session_factory: Optional[Callable[[], AsyncSession]] = None,
         **kwargs: Any

    ) -> None:
        self._sessions: Optional[SessionManager] = (
            SessionManager(session_factory) if session_factory else None
        )
        # Keep the raw factory as a plain attribute — main.py checks it via
        # `getattr(mgr, "_session_factory", None)` during startup.
        self._session_factory = session_factory
        self._initialized = False
        self._init_lock: Optional[asyncio.Lock] = None
        self._event_bus = event_bus
        
    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    # ------------------------------------------------------------------
    # Lifecycle (final — subclass hooks live in _on_initialize/_on_shutdown)
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Idempotent. Subclasses override `_on_initialize`, not this."""
        if self._initialized:
            return
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if self._initialized:          # double-checked
                return
            logger.debug("{}: initializing", self.name)
            await self._on_initialize()
            self._initialized = True
            logger.info("{}: initialized", self.name)

    def is_initialized(self) -> bool:
        """Check if the manager is initialized."""
        return self._initialized
    
    async def _on_initialize(self) -> None:
        """Override. Runs exactly once, guarded by `initialize`."""
        pass

    async def shutdown(self) -> None:
        """Idempotent. Subclasses override `_on_shutdown`, not this."""
        if not self._initialized:
            return
        try:
            await self._on_shutdown()
        finally:
            self._initialized = False
            logger.info("{}: shutdown", self.name)

    async def _on_shutdown(self) -> None:
        """Override. Runs at most once, before `_initialized` flips to False."""
        pass

    async def _ensure_initialized(self) -> None:
        """Lazy init. Call this from public entry points that may be
        reached before the lifespan startup has run."""
        if not self._initialized:
            await self.initialize()

    def get_session(self) -> Optional[AsyncSession]:
        """Get a session from the session manager, if any."""
        if self._sessions is None:
            return None
        return self._sessions.get_session()

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    @property
    def has_session(self) -> bool:
        return self._sessions is not None

    def session(self):
        """
        Async context manager yielding a scoped AsyncSession:
            async with manager.session() as s: ...

        Commits on success, rolls back on exception, closes always.
        Raises if this manager was constructed without a session_factory.
        """
        if self._sessions is None:
            raise RuntimeError(
                f"{type(self).__name__}.{self.name} has no session_factory"
            )
        return self._sessions.session()

    async def _with_session(
        self,
        fn: Callable[[AsyncSession], Awaitable[T]],
    ) -> Optional[T]:
        """
        Run `fn(session)` with commit/rollback/close.

        Returns None when there is no session_factory — tests and
        bootstrap environments don't need a DB.
        """
        if self._sessions is None:
            return None
        async with self._sessions.session() as session:
            return await fn(session)


    # ------------------------------------------------------------------
    # Status / repr
    # ------------------------------------------------------------------

    async def get_status(
        self,
        *,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Default status. Subclasses may extend via
            return {**super().get_status(...), "extra": ...}
        and may use `user_id` to include per-user info.
        """
        return {
            "name": self.name,
            "initialized": self._initialized,
            "has_session": self.has_session,
        }

    def __repr__(self) -> str:
        state = "ready" if self._initialized else "uninitialized"
        return f"<{type(self).__name__} {self.name!r} {state}>"


   