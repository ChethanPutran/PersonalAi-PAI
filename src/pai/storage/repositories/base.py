from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession


ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    """
    Base repository.

    Repositories should contain database access logic only.
    Business logic belongs in the service/orchestration layer.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session