from datetime import datetime, timedelta
import json
from typing import Dict, Any, Optional, List
from pathlib import Path
import aiosqlite
from loguru import logger

class LongTermMemory:
    """SQLite-based persistent memory for user preferences and facts."""
    
    def __init__(self, db_path: str = "./data/long_term.db"):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
    
    async def _check_connection(self):
        assert self._conn is not None, "LongTermMemory not initialized. Call initialize() first."
        
    async def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._conn.commit()
        logger.info("LongTermMemory initialized")
    
    async def set_preference(self, key: str, value: Any) -> None:
        await self._check_connection()
        async with self._conn.execute(
            "INSERT OR REPLACE INTO memories (key, value) VALUES (?, ?)",
            (key, json.dumps(value))
        ):
            await self._conn.commit()
    
    async def get_preference(self, key: str, default: Any = None) -> Any:
        await self._check_connection()
        async with self._conn.execute("SELECT value FROM memories WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row[0])
            return default
    
    async def get_relevant(self, query: str) -> List[Dict[str, Any]]:
        await self._check_connection()
        # Simple prefix search; extend with embeddings later
        async with self._conn.execute(
            "SELECT key, value FROM memories WHERE key LIKE ?", (f"{query}%",)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"key": r[0], "value": json.loads(r[1])} for r in rows]
        
    async def store(self, data: Dict[str, Any]) -> None:
        key = data.get("key") or f"memory_{hash(str(data))}"
        await self.set_preference(key, data)
    
    async def retrieve(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        await self._check_connection()
        # Simple prefix search; extend with embeddings later
        key_prefix = query.get("key_prefix", "")
        async with self._conn.execute(
            "SELECT key, value FROM memories WHERE key LIKE ?", (f"{key_prefix}%",)
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"key": r[0], "value": json.loads(r[1])} for r in rows]
    
    async def shutdown(self) -> None:
        if self._conn:
            await self._conn.close()

    async def consolidate(self):
        """Remove memories older than TTL and low importance."""
        await self._check_connection()
        cutoff = datetime.now() - timedelta(days=30)
        async with self._conn.execute(
            "DELETE FROM memories WHERE updated_at < ? AND (importance < 0.5 OR importance IS NULL)",
            (cutoff,)
        ) as cursor:
            await self._conn.commit()
            logger.info(f"Consolidated {cursor.rowcount} memories")

    async def set_importance(self, key: str, importance: float):
        await self._check_connection()
        await self._conn.execute(
            "UPDATE memories SET importance = ? WHERE key = ?",
            (importance, key)
        )
        await self._conn.commit()