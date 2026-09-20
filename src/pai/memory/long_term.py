from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger

from pai.memory.models import MemoryRecord


class LongTermMemory:
    """
    Persistent structured memory.

    Intended for:
    - User preferences
    - Stable facts
    - Important persistent information
    """

    def __init__(self, db_path: str = "./data/long_term.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def _check_connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError(
                "LongTermMemory is not initialized. "
                "Call initialize() first."
            )

        return self._conn

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(str(self.db_path))

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

        await self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_user
            ON memories(user_id)
            """
        )

        await self._conn.commit()

        logger.info("LongTermMemory initialized")

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

        conn = await self._check_connection()

        now = datetime.now(timezone.utc)

        existing = await self.get_preference_record(
            key,
            user_id=user_id,
        )

        if existing:
            existing.content = value
            existing.updated_at = now
            existing.importance = max(0.0, min(1.0, importance))

            await conn.execute(
                """
                UPDATE memories
                SET value = ?,
                    importance = ?,
                    updated_at = ?,
                    metadata = ?
                WHERE id = ?
                """,
                (
                    json.dumps(value),
                    existing.importance,
                    now.isoformat(),
                    json.dumps(metadata or existing.metadata),
                    existing.id,
                ),
            )

            await conn.commit()
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
                id,
                key,
                value,
                user_id,
                importance,
                created_at,
                updated_at,
                metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                key,
                json.dumps(value),
                user_id,
                record.importance,
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
                json.dumps(record.metadata),
            ),
        )

        await conn.commit()

        return record

    async def get_preference(
        self,
        key: str,
        default: Any = None,
        *,
        user_id: Optional[str] = None,
    ) -> Any:

        record = await self.get_preference_record(
            key,
            user_id=user_id,
        )

        return default if record is None else record.content

    async def get_preference_record(
        self,
        key: str,
        *,
        user_id: Optional[str] = None,
    ) -> Optional[MemoryRecord]:

        conn = await self._check_connection()

        if user_id is None:
            cursor = await conn.execute(
                """
                SELECT *
                FROM memories
                WHERE key = ?
                """,
                (key,),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT *
                FROM memories
                WHERE key = ?
                AND user_id = ?
                """,
                (key, user_id),
            )

        row = await cursor.fetchone()

        if row is None:
            return None

        return self._row_to_record(row)

    async def get_relevant(
        self,
        query: str,
        limit: int = 10,
        *,
        user_id: Optional[str] = None,
    ) -> List[MemoryRecord]:

        conn = await self._check_connection()

        pattern = f"%{query}%"

        if user_id is None:
            cursor = await conn.execute(
                """
                SELECT *
                FROM memories
                WHERE key LIKE ?
                ORDER BY importance DESC, updated_at DESC
                LIMIT ?
                """,
                (pattern, limit),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT *
                FROM memories
                WHERE user_id = ?
                AND key LIKE ?
                ORDER BY importance DESC, updated_at DESC
                LIMIT ?
                """,
                (user_id, pattern, limit),
            )

        rows = await cursor.fetchall()

        return [
            self._row_to_record(row)
            for row in rows
        ]

    async def store(
        self,
        data: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        importance: float = 0.5,
    ) -> MemoryRecord:

        key = data.get("key")

        if not key:
            raise ValueError(
                "LongTermMemory.store() requires data['key']"
            )

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

        key_prefix = query.get("key_prefix", "")
        user_id = query.get("user_id")
        limit = query.get("limit", 10)

        conn = await self._check_connection()

        pattern = f"{key_prefix}%"

        if user_id is None:
            cursor = await conn.execute(
                """
                SELECT *
                FROM memories
                WHERE key LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (pattern, limit),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT *
                FROM memories
                WHERE key LIKE ?
                AND user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (pattern, user_id, limit),
            )

        rows = await cursor.fetchall()

        return [
            self._row_to_record(row)
            for row in rows
        ]

    async def set_importance(
        self,
        key: str,
        importance: float,
    ) -> None:

        conn = await self._check_connection()

        importance = max(0.0, min(1.0, importance))

        await conn.execute(
            """
            UPDATE memories
            SET importance = ?,
                updated_at = ?
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

        conn = await self._check_connection()

        cutoff = datetime.now(timezone.utc).timestamp() - (
            max_age_days * 86400
        )

        cursor = await conn.execute(
            "SELECT id, updated_at, importance FROM memories"
        )

        rows = await cursor.fetchall()

        delete_ids = []

        for memory_id, updated_at, importance in rows:
            try:
                timestamp = datetime.fromisoformat(
                    updated_at
                ).timestamp()
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

        logger.info(
            "Long-term memory consolidated: {} removed",
            len(delete_ids),
        )

        return len(delete_ids)

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