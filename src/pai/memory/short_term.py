from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from pai.config.models.memory import ShortTermBackend, ShortTermMemoryConfig
from pai.memory.models import MemoryRecord
from pai.observability.tracer import get_tracer


class ShortTermMemory:
    """
    In-memory working memory for the current session.

    Characteristics:
      - Fast
      - Bounded by max_size
      - TTL-based
      - Not persistent
    """

    def __init__(
        self,
        config:ShortTermMemoryConfig
    ):
        self.max_size = config.size
        self.ttl_seconds = config.ttl_seconds

        self._store: deque[MemoryRecord] = deque(maxlen=self.max_size)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        logger.info(
            "ShortTermMemory initialized "
            "(max_size={}, ttl={}s)", self.max_size, self.ttl_seconds,
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def add(
        self,
        content: Any,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        tracer = get_tracer()

        async with tracer.span(
            "short_term.add",
            session_id=session_id,
            user_id=user_id,
        ) as span:
            record = MemoryRecord(
                content=content,
                memory_type="short_term",
                user_id=user_id,
                session_id=session_id,
                importance=importance,
                metadata=metadata or {},
            )

            t0 = time.perf_counter()
            async with self._lock:
                wait_ms = (time.perf_counter() - t0) * 1000
                self._purge_expired()
                self._store.append(record)
                size_after = len(self._store)

            span.set_attribute("record_id", record.id)
            span.set_attribute("store_size", size_after)
            span.set_attribute(
                "lock_wait_ms", round(wait_ms, 2),
            )
            return record

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_recent(
        self,
        limit: int = 10,
        *,
        user_id: Optional[str],
        session_id: Optional[str] = None,
    ) -> List[MemoryRecord]:
        """Recent working memory of ONE user (optionally one session).

        user_id is mandatory — the buffer is shared by all users in the process.
        """
        if limit <= 0:
            return []
        if not user_id:
            raise ValueError("user_id is required: memory reads are always scoped to one user")

        tracer = get_tracer()
        async with tracer.span(
            "short_term.get_recent",
            limit=limit,
            session_id=session_id,
        ) as span:
            t0 = time.perf_counter()
            async with self._lock:
                wait_ms = (time.perf_counter() - t0) * 1000
                self._purge_expired()

                records = [
                    r for r in self._store
                    if r.user_id == user_id and not r.metadata.get("quarantined")
                ]

                if session_id is not None:
                    records = [
                        r for r in records if r.session_id == session_id
                    ]

                result = records[-limit:][::-1]

            span.set_attribute("store_size", len(self._store))
            span.set_attribute("returned", len(result))
            span.set_attribute(
                "lock_wait_ms", round(wait_ms, 2),
            )
            return result

    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        # Cheap linear scan inside the lock. Only add a span if it ever
        # becomes a bottleneck — right now it's <50µs.
        async with self._lock:
            self._purge_expired()
            for record in self._store:
                if record.id == memory_id:
                    return record
        return None

    # ------------------------------------------------------------------
    # Clear / shutdown
    # ------------------------------------------------------------------

    async def clear(
        self,
        *,
        session_id: Optional[str] = None,
    ) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "short_term.clear",
            session_id=session_id,
        ) as span:
            async with self._lock:
                before = len(self._store)

                if session_id is None:
                    self._store.clear()
                else:
                    remaining = [
                        r for r in self._store
                        if r.session_id != session_id
                    ]
                    self._store = deque(remaining, maxlen=self.max_size)

            span.set_attribute("cleared", before - len(self._store))
            span.set_attribute("remaining", len(self._store))

    def _purge_expired(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        valid = deque(
            (
                r for r in self._store
                if now - r.created_at.timestamp() <= self.ttl_seconds
            ),
            maxlen=self.max_size,
        )
        self._store = valid

    async def shutdown(self) -> None:
        await self.clear()
        logger.info("ShortTermMemory shutdown")


def create_short_term_memory(
    config: ShortTermMemoryConfig,
) -> ShortTermMemory:
    """Factory function to create a ShortTermMemory instance."""
    if config.backend != ShortTermBackend.MEMORY:
        raise NotImplementedError(
            f"Short-term memory backend '{config.backend.value}' is not supported yet."
        )
    return ShortTermMemory(config=config)