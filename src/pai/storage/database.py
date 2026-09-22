from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


class Database:
    """
    Async SQLAlchemy database manager.

    Two ways to obtain a session:

      # Context manager (routes, one-shot operations)
      async with db.session() as s:
          ...
          # auto-commit on success, auto-rollback on error

      # Raw factory (services that need explicit transaction control)
      s = db.factory()
      try:
          ...
          await s.commit()
      finally:
          await s.close()
    """

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self.echo = echo

        self.engine: AsyncEngine = create_async_engine(
            url,
            echo=echo,
            future=True,
            pool_pre_ping=True,
        )

        self._session_factory: async_sessionmaker[AsyncSession] = (
            async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
        )

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Create database tables.

        Importing pai.storage.models here is REQUIRED — without it,
        the ORM models won't be registered on Base.metadata and
        their tables won't be created.
        """
        # noqa justification: import must happen before create_all
        from pai.storage import models  # noqa: F401

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """
        Per-request session context manager.

        Commits on success, rolls back on exception, always closes.
        """
        session = self._session_factory()
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
        Return a fresh AsyncSession.

        Caller is responsible for commit / rollback / close.
        Used by services (PluginManager, SecurityManager) that
        prefer explicit transaction control.
        """
        return self._session_factory()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Dispose the engine and all pooled connections."""
        await self.engine.dispose()