from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger

from pai.memory.models import MemoryRecord
from pai.memory.utils import _json_default
from pai.observability.tracer import get_tracer


class EpisodicMemory:
    """
    Persistent memory of past interactions.

    Owns the episode records. Semantic retrieval is delegated to
    VectorStore.
    """

    def __init__(self, db_path: str = "./data/episodic.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def _check_connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("EpisodicMemory is not initialized.")
        return self._conn

    async def initialize(self) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "episodic.initialize", db_path=str(self.db_path),
        ) as span:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(str(self.db_path))

            try:
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
                # ── Migration: old schema was missing these ──
                await self._ensure_column("episodes", "user_id", "TEXT")
                await self._ensure_column("episodes", "session_id", "TEXT")
                await self._ensure_column(
                    "episodes", "importance", "REAL NOT NULL DEFAULT 0.5"
                )
                await self._ensure_column(
                    "episodes", "metadata", "TEXT NOT NULL DEFAULT '{}'"
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
            except Exception as exc:
                span.set_error(f"{type(exc).__name__}: {exc}")
                raise

            logger.info("EpisodicMemory initialized")


    async def _ensure_column(
        self, table: str, column: str, ddl_type: str,
    ) -> None:
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
            "episodic.add",
            user_id=user_id,
            session_id=session_id,
            content_type=type(content).__name__,
        ) as span:
            record = MemoryRecord(
                content=content,
                memory_type="episodic",
                user_id=user_id,
                session_id=session_id,
                importance=importance,
                metadata=metadata or {},
            )

            conn = await self._check_connection()
            t0 = time.perf_counter()

            try:
                await conn.execute(
                    """
                    INSERT INTO episodes (
                        id, content, user_id, session_id,
                        created_at, importance, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id, json.dumps(content, default=_json_default), user_id, session_id,
                        record.created_at.isoformat(), record.importance,
                        json.dumps(record.metadata, default=_json_default),
                    ),
                )
                await conn.commit()
            except Exception as exc:
                span.set_error(f"{type(exc).__name__}: {exc}")
                raise

            span.set_attribute("record_id", record.id)
            span.set_attribute(
                "duration_ms", round((time.perf_counter() - t0) * 1000, 1),
            )
            return record

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        tracer = get_tracer()
        async with tracer.span(
            "episodic.get",
            memory_id=memory_id,
        ) as span:
            conn = await self._check_connection()
            cursor = await conn.execute(
                "SELECT * FROM episodes WHERE id = ?",
                (memory_id,),
            )
            row = await cursor.fetchone()
            span.set_attribute("found", row is not None)
            return None if row is None else self._row_to_record(row)

    async def get_recent(
        self,
        limit: int = 10,
        *,
        user_id: Optional[str] = None,
    ) -> List[MemoryRecord]:
        tracer = get_tracer()
        async with tracer.span(
            "episodic.get_recent",
            limit=limit,
            user_id=user_id,
        ) as span:
            conn = await self._check_connection()
            t0 = time.perf_counter()

            if user_id is None:
                cursor = await conn.execute(
                    "SELECT * FROM episodes ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM episodes
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                )

            rows = await cursor.fetchall()
            span.set_attribute("result_count", len(rows))
            span.set_attribute(
                "query_ms", round((time.perf_counter() - t0) * 1000, 1),
            )
            return [self._row_to_record(row) for row in rows]

    async def search_text(
        self,
        query: str,
        limit: int = 10,
        *,
        user_id: Optional[str] = None,
    ) -> List[MemoryRecord]:
        tracer = get_tracer()
        async with tracer.span(
            "episodic.search_text",
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
                    SELECT * FROM episodes
                    WHERE content LIKE ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (pattern, limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM episodes
                    WHERE user_id = ? AND content LIKE ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (user_id, pattern, limit),
                )

            rows = await cursor.fetchall()
            span.set_attribute("result_count", len(rows))
            span.set_attribute(
                "query_ms", round((time.perf_counter() - t0) * 1000, 1),
            )
            return [self._row_to_record(row) for row in rows]

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