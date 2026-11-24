"""Async SQLAlchemy database manager with session + transaction tracing."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from pai.config.models.database import DatabaseBackend, DatabaseConfig
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

    def __init__(self, config: DatabaseConfig) -> None:
        self.url = config.url
        self.echo = config.echo

        self.engine: AsyncEngine = create_async_engine(
            self.url,
            echo=self.echo,
            future=True,
            pool_pre_ping=True,
        )

        if self.url.startswith("sqlite"):
            # Many writers (DAG nodes, chat, tasks, traces) share one file. SQLite's default
            # rollback journal serializes them and fails fast with "database is locked".
            # WAL lets readers and one writer run together; busy_timeout makes a writer wait
            # (up to 5 s) for its turn instead of failing.
            @event.listens_for(self.engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record) -> None:   # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                try:
                    cur.execute("PRAGMA journal_mode=WAL")
                    cur.execute("PRAGMA busy_timeout=5000")
                    cur.execute("PRAGMA synchronous=NORMAL")
                finally:
                    cur.close()

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
                if self.url.startswith("sqlite"):
                    # create_all does not add columns to existing tables.
                    from pai.storage.migrations import upgrade_sqlite_schema
                    await upgrade_sqlite_schema(connection)

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


def create_database(config: DatabaseConfig) -> Database:
    """Create a Database instance from a config."""
    if config.backend == DatabaseBackend.SQLITE:
        return Database(config)
    else:
        raise ValueError(f"Unsupported database backend: {config.backend}")