from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
from loguru import logger

from pai.memory.models import MemoryRecord
from pai.memory.utils import _json_default
from pai.observability.tracer import get_tracer


class LongTermMemory:
    """
    Persistent structured memory for user preferences and stable facts.
    """

    def __init__(self, db_path: str = "./data/long_term.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def _check_connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError(
                "LongTermMemory is not initialized. Call initialize() first."
            )
        return self._conn

    async def initialize(self) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "long_term.initialize", db_path=str(self.db_path),
        ) as span:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(str(self.db_path))

            try:
                await self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        id TEXT PRIMARY KEY,
                        key TEXT NOT NULL UNIQUE,
                        value TEXT NOT NULL,
                        user_id TEXT,
                        importance REAL NOT NULL DEFAULT 0.5,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                # ── Migration: add user_id if the old schema lacks it ──
                await self._ensure_column("memories", "user_id", "TEXT")
                await self._ensure_column("memories", "importance", "REAL NOT NULL DEFAULT 0.5")

                await self._conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_memories_user
                    ON memories(user_id)
                    """
                )
                await self._conn.commit()
            except Exception as exc:
                span.set_error(f"{type(exc).__name__}: {exc}")
                raise

            logger.info("LongTermMemory initialized")


    async def _ensure_column(
        self, table: str, column: str, ddl_type: str,
    ) -> None:
        """Add [column] to [table] if it doesn't exist. Idempotent."""
        cursor = await self._conn.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in await cursor.fetchall()}
        if column not in cols:
            logger.warning(
                "Migrating {}.{}: adding missing column ({})",
                table, column, ddl_type,
            )
            await self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"
            )

    async def set_preference(
        self,
        key: str,
        value: Any,
        *,
        user_id: Optional[str] = None,
        importance: float = 0.8,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        if not key:
            raise ValueError("Preference key cannot be empty")

        tracer = get_tracer()
        async with tracer.span(
            "long_term.set_preference",
            key=key,
            user_id=user_id,
            importance=importance,
        ) as span:
            conn = await self._check_connection()
            now = datetime.now(timezone.utc)

            existing = await self.get_preference_record(key, user_id=user_id)

            t0 = time.perf_counter()
            try:
                if existing:
                    existing.content = value
                    existing.updated_at = now
                    existing.importance = max(0.0, min(1.0, importance))

                    await conn.execute(
                        """
                        UPDATE memories
                        SET value = ?, importance = ?, updated_at = ?, metadata = ?
                        WHERE id = ?
                        """,
                        (
                            json.dumps(value, default=_json_default),
                            existing.importance,
                            now.isoformat(),
                            json.dumps(metadata or existing.metadata, default=_json_default),
                            existing.id,
                        ),
                    )
                    await conn.commit()
                    span.set_attribute("was_update", True)
                    span.set_attribute("record_id", existing.id)
                    return existing

                record = MemoryRecord(
                    content=value,
                    memory_type="long_term",
                    user_id=user_id,
                    importance=importance,
                    metadata=metadata or {},
                )

                await conn.execute(
                    """
                    INSERT INTO memories (
                        id, key, value, user_id, importance,
                        created_at, updated_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id, key, json.dumps(value, default=_json_default), user_id,
                        record.importance,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                        json.dumps(record.metadata, default=_json_default),
                    ),
                )
                await conn.commit()
                span.set_attribute("was_update", False)
                span.set_attribute("record_id", record.id)
                return record

            except Exception as exc:
                span.set_error(f"{type(exc).__name__}: {exc}")
                raise

            finally:
                span.set_attribute(
                    "duration_ms",
                    round((time.perf_counter() - t0) * 1000, 1),
                )

    async def get_preference(
        self,
        key: str,
        default: Any = None,
        *,
        user_id: Optional[str] = None,
    ) -> Any:
        tracer = get_tracer()
        async with tracer.span(
            "long_term.get_preference",
            key=key,
            user_id=user_id,
        ) as span:
            record = await self.get_preference_record(key, user_id=user_id)
            span.set_attribute("found", record is not None)
            return default if record is None else record.content

    async def get_preference_record(
        self,
        key: str,
        *,
        user_id: Optional[str] = None,
    ) -> Optional[MemoryRecord]:
        tracer = get_tracer()
        async with tracer.span(
            "long_term.get_preference_record",
            key=key,
            user_id=user_id,
        ) as span:
            conn = await self._check_connection()

            if user_id is None:
                cursor = await conn.execute(
                    "SELECT * FROM memories WHERE key = ?",
                    (key,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM memories WHERE key = ? AND user_id = ?",
                    (key, user_id),
                )

            row = await cursor.fetchone()
            span.set_attribute("found", row is not None)
            return None if row is None else self._row_to_record(row)

    async def get_relevant(
        self,
        query: str,
        limit: int = 10,
        *,
        user_id: Optional[str] = None,
    ) -> List[MemoryRecord]:
        tracer = get_tracer()
        async with tracer.span(
            "long_term.get_relevant",
            query_length=len(query),
            user_id=user_id,
            limit=limit,
        ) as span:
            conn = await self._check_connection()
            pattern = f"%{query}%"

            t0 = time.perf_counter()
            if user_id is None:
                cursor = await conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE key LIKE ?
                    ORDER BY importance DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (pattern, limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE user_id = ? AND key LIKE ?
                    ORDER BY importance DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (user_id, pattern, limit),
                )

            rows = list(await cursor.fetchall())
            span.set_attribute("result_count", len(rows))
            span.set_attribute(
                "query_ms", round((time.perf_counter() - t0) * 1000, 1),
            )
            return [self._row_to_record(row) for row in rows]

    async def store(
        self,
        data: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        importance: float = 0.5,
    ) -> MemoryRecord:
        key = data.get("key")
        if not key:
            raise ValueError("LongTermMemory.store() requires data['key']")

        # set_preference already traces; don't double-wrap
        return await self.set_preference(
            key,
            data.get("value", data),
            user_id=user_id,
            importance=importance,
            metadata=data.get("metadata", {}),
        )

    async def retrieve(
        self,
        query: Dict[str, Any],
    ) -> List[MemoryRecord]:
        tracer = get_tracer()
        async with tracer.span(
            "long_term.retrieve",
            key_prefix=query.get("key_prefix", ""),
            user_id=query.get("user_id"),
        ) as span:
            key_prefix = query.get("key_prefix", "")
            user_id = query.get("user_id")
            limit = query.get("limit", 10)

            conn = await self._check_connection()
            pattern = f"{key_prefix}%"

            if user_id is None:
                cursor = await conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE key LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (pattern, limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE key LIKE ? AND user_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (pattern, user_id, limit),
                )

            rows = await cursor.fetchall()
            span.set_attribute("result_count", len(rows))
            return [self._row_to_record(row) for row in rows]

    async def set_importance(
        self,
        key: str,
        importance: float,
    ) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "long_term.set_importance",
            key=key,
            importance=importance,
        ):
            conn = await self._check_connection()
            importance = max(0.0, min(1.0, importance))

            await conn.execute(
                """
                UPDATE memories
                SET importance = ?, updated_at = ?
                WHERE key = ?
                """,
                (
                    importance,
                    datetime.now(timezone.utc).isoformat(),
                    key,
                ),
            )
            await conn.commit()

    async def consolidate(
        self,
        *,
        max_age_days: int = 30,
        min_importance: float = 0.2,
    ) -> int:
        tracer = get_tracer()
        async with tracer.span(
            "long_term.consolidate",
            max_age_days=max_age_days,
            min_importance=min_importance,
        ) as span:
            conn = await self._check_connection()
            cutoff = (
                datetime.now(timezone.utc).timestamp()
                - (max_age_days * 86400)
            )

            cursor = await conn.execute(
                "SELECT id, updated_at, importance FROM memories"
            )
            rows = await cursor.fetchall()

            delete_ids = []
            for memory_id, updated_at, importance in rows:
                try:
                    timestamp = datetime.fromisoformat(updated_at).timestamp()
                except ValueError:
                    continue
                if timestamp < cutoff and importance < min_importance:
                    delete_ids.append(memory_id)

            if delete_ids:
                await conn.executemany(
                    "DELETE FROM memories WHERE id = ?",
                    [(memory_id,) for memory_id in delete_ids],
                )
                await conn.commit()

            span.set_attribute("removed", len(delete_ids))

            logger.info(
                "Long-term memory consolidated: {} removed",
                len(delete_ids),
            )
            removed = len(delete_ids)
            return  removed 

    def _row_to_record(self, row: tuple) -> MemoryRecord:
        return MemoryRecord(
            id=row[0],
            memory_type="long_term",
            content=json.loads(row[2]),
            user_id=row[3],
            importance=row[4],
            created_at=datetime.fromisoformat(row[5]),
            updated_at=datetime.fromisoformat(row[6]),
            metadata=json.loads(row[7]),
        )

    async def shutdown(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
        logger.info("LongTermMemory shutdown")