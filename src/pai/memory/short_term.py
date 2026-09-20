from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from pai.memory.models import MemoryRecord


class ShortTermMemory:
    """
    In-memory working memory for the current session.

    Characteristics:
    - Fast
    - Bounded by max_size
    - TTL based
    - Not persistent
    """

    def __init__(
        self,
        max_size: int = 100,
        ttl_seconds: int = 3600,
    ):
        if max_size <= 0:
            raise ValueError("max_size must be greater than zero")

        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")

        self.max_size = max_size
        self.ttl_seconds = ttl_seconds

        self._store: deque[MemoryRecord] = deque(maxlen=max_size)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        logger.info(
            "ShortTermMemory initialized "
            f"(max_size={self.max_size}, ttl={self.ttl_seconds}s)"
        )

    async def add(
        self,
        content: Any,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:

        record = MemoryRecord(
            content=content,
            memory_type="short_term",
            user_id=user_id,
            session_id=session_id,
            importance=importance,
            metadata=metadata or {},
        )

        async with self._lock:
            self._purge_expired()
            self._store.append(record)

        return record

    async def get_recent(
        self,
        limit: int = 10,
        *,
        session_id: Optional[str] = None,
    ) -> List[MemoryRecord]:

        if limit <= 0:
            return []

        async with self._lock:
            self._purge_expired()

            records = list(self._store)

            if session_id is not None:
                records = [
                    record
                    for record in records
                    if record.session_id == session_id
                ]

            return records[-limit:][::-1]

    async def get(
        self,
        memory_id: str,
    ) -> Optional[MemoryRecord]:

        async with self._lock:
            self._purge_expired()

            for record in self._store:
                if record.id == memory_id:
                    return record

        return None

    async def clear(
        self,
        *,
        session_id: Optional[str] = None,
    ) -> None:

        async with self._lock:
            if session_id is None:
                self._store.clear()
                return

            remaining = [
                record
                for record in self._store
                if record.session_id != session_id
            ]

            self._store = deque(
                remaining,
                maxlen=self.max_size,
            )

    def _purge_expired(self) -> None:
        now = datetime.now(timezone.utc).timestamp()

        valid = deque(
            (
                record
                for record in self._store
                if now - record.created_at.timestamp() <= self.ttl_seconds
            ),
            maxlen=self.max_size,
        )

        self._store = valid

    async def shutdown(self) -> None:
        await self.clear()
        logger.info("ShortTermMemory shutdown")