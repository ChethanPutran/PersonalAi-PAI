from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger

from pai.config.models.memory import EpisodicMemoryConfig, LongTermBackend
from pai.memory.models import MemoryRecord
from pai.memory.utils import _json_default, keyword_tokens, like_pattern
from pai.observability.tracer import get_tracer


def _require_user(user_id: Optional[str]) -> None:
    if not user_id:
        raise ValueError("user_id is required: memory reads are always scoped to one user")


class EpisodicMemory:
    """
    Persistent memory of past interactions.

    Owns the episode records. Semantic retrieval is delegated to
    VectorStore.
    """

    def __init__(self, config: EpisodicMemoryConfig):
        self.db_path = Path(config.db_url)
        self._conn: Optional[aiosqlite.Connection] = None
        self.config = config
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

    async def search_text(
        self,
        query: str,
        limit: int = 10,
        *,
        user_id: Optional[str],
    ) -> List[MemoryRecord]:
        """Episodes of ONE user matching any keyword of ``query`` (newest first).

        user_id is mandatory: a missing id must never widen the search to everyone.
        Quarantined episodes (blocked by the memory guard) are never returned.
        """
        _require_user(user_id)
        tracer = get_tracer()
        async with tracer.span(
            "episodic.search_text",
            query_length=len(query),
            user_id=user_id,
            limit=limit,
        ) as span:
            tokens = keyword_tokens(query)
            if not tokens:
                return await self.get_recent(limit, user_id=user_id)

            conn = await self._check_connection()
            t0 = time.perf_counter()
            clause = " OR ".join("content LIKE ? ESCAPE '\\'" for _ in tokens)
            cursor = await conn.execute(
                f"""
                SELECT * FROM episodes
                WHERE user_id = ? AND ({clause})
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, *[like_pattern(t) for t in tokens], limit * 3),
            )
            rows = await cursor.fetchall()
            records = [r for r in map(self._row_to_record, rows)
                       if not r.metadata.get("quarantined")][:limit]
            span.set_attribute("result_count", len(records))
            span.set_attribute(
                "query_ms", round((time.perf_counter() - t0) * 1000, 1),
            )
            return records

    async def get_recent(
        self,
        limit: int = 10,
        *,
        user_id: Optional[str],
    ) -> List[MemoryRecord]:
        _require_user(user_id)
        tracer = get_tracer()
        async with tracer.span(
            "episodic.get_recent",
            limit=limit,
            user_id=user_id,
        ) as span:
            conn = await self._check_connection()
            t0 = time.perf_counter()
            cursor = await conn.execute(
                """
                SELECT * FROM episodes
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit * 3),
            )
            rows = await cursor.fetchall()
            records = [r for r in map(self._row_to_record, rows)
                       if not r.metadata.get("quarantined")][:limit]
            span.set_attribute("result_count", len(records))
            span.set_attribute(
                "query_ms", round((time.perf_counter() - t0) * 1000, 1),
            )
            return records

    async def count(self, user_id: str) -> int:
        _require_user(user_id)
        conn = await self._check_connection()
        cursor = await conn.execute("SELECT COUNT(*) FROM episodes WHERE user_id = ?", (user_id,))
        return int((await cursor.fetchone())[0])

    async def prune(self, *, max_age_days: int, max_per_user: int) -> int:
        """Retention: drop episodes older than ``max_age_days`` and keep at most
        ``max_per_user`` newest per user. Returns rows deleted."""
        conn = await self._check_connection()
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        cursor = await conn.execute("SELECT id, user_id, created_at FROM episodes ORDER BY created_at DESC")
        per_user: Dict[Optional[str], int] = {}
        doomed: List[str] = []
        for eid, uid, created in await cursor.fetchall():
            try:
                too_old = datetime.fromisoformat(created).timestamp() < cutoff
            except ValueError:
                too_old = False
            per_user[uid] = per_user.get(uid, 0) + 1
            if too_old or per_user[uid] > max_per_user:
                doomed.append(eid)
        if doomed:
            await conn.executemany("DELETE FROM episodes WHERE id = ?", [(i,) for i in doomed])
            await conn.commit()
        return len(doomed)

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



def create_episodic_memory(config: EpisodicMemoryConfig) -> EpisodicMemory:
    """Factory function to create an EpisodicMemory instance."""
    if config.backend == LongTermBackend.SQLITE:
        return EpisodicMemory(config)
    else:
        raise ValueError(f"Unsupported backend: {config.backend}")