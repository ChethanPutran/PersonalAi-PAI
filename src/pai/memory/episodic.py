from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger

from pai.memory.models import MemoryRecord


class EpisodicMemory:
    """
    Persistent memory of past interactions and experiences.

    EpisodicMemory owns the episode records.
    Semantic retrieval can be performed through VectorStore.
    """

    def __init__(
        self,
        db_path: str = "./data/episodic.db",
    ):
        self.db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def _check_connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError(
                "EpisodicMemory is not initialized."
            )

        return self._conn

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(str(self.db_path))

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                user_id TEXT,
                session_id TEXT,
                created_at TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0.5,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )

        await self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episodes_user
            ON episodes(user_id)
            """
        )

        await self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episodes_session
            ON episodes(session_id)
            """
        )

        await self._conn.commit()

        logger.info("EpisodicMemory initialized")

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
            memory_type="episodic",
            user_id=user_id,
            session_id=session_id,
            importance=importance,
            metadata=metadata or {},
        )

        conn = await self._check_connection()

        await conn.execute(
            """
            INSERT INTO episodes (
                id,
                content,
                user_id,
                session_id,
                created_at,
                importance,
                metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                json.dumps(content),
                user_id,
                session_id,
                record.created_at.isoformat(),
                record.importance,
                json.dumps(record.metadata),
            ),
        )

        await conn.commit()

        return record

    async def get(
        self,
        memory_id: str,
    ) -> Optional[MemoryRecord]:

        conn = await self._check_connection()

        cursor = await conn.execute(
            """
            SELECT *
            FROM episodes
            WHERE id = ?
            """,
            (memory_id,),
        )

        row = await cursor.fetchone()

        if row is None:
            return None

        return self._row_to_record(row)

    async def get_recent(
        self,
        limit: int = 10,
        *,
        user_id: Optional[str] = None,
    ) -> List[MemoryRecord]:

        conn = await self._check_connection()

        if user_id is None:
            cursor = await conn.execute(
                """
                SELECT *
                FROM episodes
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT *
                FROM episodes
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            )

        rows = await cursor.fetchall()

        return [
            self._row_to_record(row)
            for row in rows
        ]

    async def search_text(
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
                FROM episodes
                WHERE content LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (pattern, limit),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT *
                FROM episodes
                WHERE user_id = ?
                AND content LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, pattern, limit),
            )

        rows = await cursor.fetchall()

        return [
            self._row_to_record(row)
            for row in rows
        ]

    def _row_to_record(self, row: tuple) -> MemoryRecord:
        return MemoryRecord(
            id=row[0],
            memory_type="episodic",
            content=json.loads(row[1]),
            user_id=row[2],
            session_id=row[3],
            created_at=datetime.fromisoformat(row[4]),
            importance=row[5],
            metadata=json.loads(row[6]),
        )

    async def shutdown(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

        logger.info("EpisodicMemory shutdown")