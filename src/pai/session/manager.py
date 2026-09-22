from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

from sqlalchemy.ext.asyncio import AsyncSession



class SessionManager:
    """
    Factory for per-request AsyncSessions.

    Two uses:

      # 1. As an async context manager (services, background work)
      async with session_manager.session() as s:
          ...

      # 2. As a callable factory (dependency injection, services
          that need to open sessions lazily)
      factory: Callable[[], AsyncSession] = session_manager.factory
    """

    def __init__(self, session_factory: Callable[[], AsyncSession]):
        self._session_factory = session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """
        Yield a fresh AsyncSession.

        Commits on success, rolls back on exception, closes always.
        """
        sm = self._session_factory
        session = sm()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    def factory(self) -> AsyncSession:
        """
        Return a raw AsyncSession. Callers are responsible for
        commit / rollback / close.

        Used by services that prefer explicit transaction control,
        e.g. PluginManager opening a session per operation.
        """
        return self._session_factory()