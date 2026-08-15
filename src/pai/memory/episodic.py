import json
from typing import Dict, Any, List
import aiosqlite
from loguru import logger

import numpy as np

class EpisodicMemory:
    """Stores past interactions as episodes for experience recall."""
    
    def __init__(self, db_url: str = "sqlite+aiosqlite:///./data/episodic.db"):
        self.db_url = db_url
        self._conn = None
        self.model = None
    
    async def _check_connection(self):
        assert self._conn is not None, "EpisodicMemory not initialized"

    def _get_model(self):
        if self.model is not None:
            return self.model

        try:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer("all-MiniLM-L6-v2")
            return self.model
        except Exception as exc:
            logger.warning(
                "Semantic episodic memory disabled because sentence-transformers "
                f"could not be loaded: {exc}"
            )
            return None

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self.db_url)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._ensure_embedding_column()
        logger.info("EpisodicMemory initialized")
    
    async def _ensure_embedding_column(self) -> None:
        await self._check_connection()
        try:
            await self._conn.execute("ALTER TABLE episodes ADD COLUMN embedding BLOB") # type: ignore
        except aiosqlite.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

        await self._conn.commit()
    
    async def add_base(self, episode: Dict[str, Any]) -> None:
        assert self._conn is not None
        await self._check_connection()
        await self._conn.execute( 
            "INSERT INTO episodes (content) VALUES (?)",
            (json.dumps(episode),)
        ) 
        await self._conn.commit()

        
    async def add(self, episode: Dict[str, Any]) -> None:
        model = self._get_model()
        if model is None:
            await self.add_base(episode)
            return

        embedding = model.encode(json.dumps(episode))
        await self._check_connection()
        await self._conn.execute(
            "INSERT INTO episodes (content, embedding) VALUES (?, ?)",
            (json.dumps(episode), embedding.tobytes())
        )
        await self._conn.commit()
    
    async def get_similar(self, query_text: str, limit: int = 5) -> List[Dict]:
        await self._check_connection()
        model = self._get_model()
        if model is None:
            return await self.get_similar_db(query_text, limit)

        query_vec = model.encode(query_text)
        async with self._conn.execute(
            "SELECT id, content, embedding FROM episodes WHERE embedding IS NOT NULL"
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return await self.get_similar_db(query_text, limit)

        scores = []
        for row in rows:
            stored_embedding = np.frombuffer(row[2], dtype=np.float32)
            sim = np.dot(query_vec, stored_embedding) / (np.linalg.norm(query_vec) * np.linalg.norm(stored_embedding))
            scores.append((sim, json.loads(row[1])))
        scores.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scores[:limit]]
    
    async def get_similar_db(self, query_text: str, limit: int = 5) -> List[Dict[str, Any]]:
        await self._check_connection()
        # Simple keyword matching; for production, use embeddings
        async with self._conn.execute(
            "SELECT content FROM episodes ORDER BY timestamp DESC LIMIT ?", (limit * 3,)
        ) as cursor:
            rows = await cursor.fetchall()
            episodes = [json.loads(r[0]) for r in rows]
            # Simple relevance scoring: count query words in content string
            words = set(query_text.lower().split())
            scored = [(e, sum(word in str(e).lower() for word in words)) for e in episodes]
            scored.sort(key=lambda x: x[1], reverse=True)
            return [e for e, _ in scored[:limit]]
    
    async def shutdown(self) -> None:
        if self._conn:
            await self._conn.close()
