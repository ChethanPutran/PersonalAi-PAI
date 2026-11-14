"""Async SQLAlchemy database manager with session + transaction tracing."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from pai.observability.tracer import get_tracer


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
        tracer = get_tracer()
        async with tracer.span(
            "db.initialize",
            url=self.url.split("@")[-1],   # strip credentials from URL
        ) as span:
            # noqa justification: import must happen before create_all
            from pai.storage import models  # noqa: F401

            t0 = time.perf_counter()
            async with self.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            span.set_attribute(
                "duration_ms",
                round((time.perf_counter() - t0) * 1000, 1),
            )
            span.set_attribute(
                "table_count", len(Base.metadata.tables),
            )

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """
        Per-request session context manager.

        Commits on success, rolls back on exception, always closes.

        Traced as `db.session`: the span covers the entire block (so
        child spans nest inside), but separate attributes expose how
        much of the total is body work vs commit/rollback overhead.
        """
        tracer = get_tracer()

        async with tracer.span("db.session") as span:
            t_start = time.perf_counter()
            session = self._session_factory()

            try:
                yield session

                # Time the commit separately — flushes are often the
                # real cost in a write-heavy endpoint.
                t_commit = time.perf_counter()
                await session.commit()
                commit_ms = (time.perf_counter() - t_commit) * 1000

                span.set_attribute("outcome", "commit")
                span.set_attribute("commit_ms", round(commit_ms, 1))
                span.set_attribute(
                    "total_ms",
                    round((time.perf_counter() - t_start) * 1000, 1),
                )

            except Exception as exc:
                await session.rollback()
                span.set_attribute("outcome", "rollback")
                span.set_error(f"{type(exc).__name__}: {exc}")
                raise

            finally:
                await session.close()

    def factory(self) -> AsyncSession:
        """
        Return a fresh AsyncSession.

        Caller is responsible for commit / rollback / close.
        Used by services (PluginManager, SecurityManager) that
        prefer explicit transaction control.

        Not traced — callers are responsible for wrapping their own
        transaction in a span (see `_with_session` in PluginManager).
        """
        return self._session_factory()

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        """
        Expose the async_sessionmaker for services that need their own
        transaction boundaries (TaskRepository, SchedulerService, …).

        Do NOT pass `database.factory` instead — that returns a single
        AsyncSession instance, not a factory.
        """
        return self._session_factory

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Dispose the engine and all pooled connections."""
        tracer = get_tracer()
        async with tracer.span("db.close"):
            await self.engine.dispose()