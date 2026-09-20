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

    Responsibilities:
    - Create and manage the database engine.
    - Provide async sessions.
    - Initialize database tables.
    - Dispose the engine during application shutdown.
    """

    def __init__(
        self,
        url: str,
        *,
        echo: bool = False,
    ) -> None:
        self.url = url
        self.echo = echo

        self.engine: AsyncEngine = create_async_engine(
            url,
            echo=echo,
            future=True,
        )

        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def initialize(self) -> None:
        """
        Create database tables.

        For production deployments, migrations should eventually be
        handled by Alembic instead of create_all().
        """
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """
        Provide a transactional database session.

        Commits on successful completion and rolls back if an exception
        occurs.
        """
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def close(self) -> None:
        """Dispose the database engine."""
        await self.engine.dispose()