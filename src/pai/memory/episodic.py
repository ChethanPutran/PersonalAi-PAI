import json
from typing import Dict, Any, List
import aiosqlite
from loguru import logger

from sentence_transformers import SentenceTransformer
import numpy as np
import aiosqlite
import json

class EpisodicMemory:
    """Stores past interactions as episodes for experience recall."""
    
    def __init__(self, db_path: str = "./data/episodic.db"):
        self.db_path = db_path
        self._conn = None
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
    
    async def _check_connection(self):
        assert self._conn is not None, "EpisodicMemory not initialized"

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._conn.commit()
        logger.info("EpisodicMemory initialized")
    
    async def add_base(self, episode: Dict[str, Any]) -> None:
        await self._check_connection()
        await self._conn.execute( 
            "INSERT INTO episodes (content) VALUES (?)",
            (json.dumps(episode),)
        ) 
        await self._conn.commit()

        
    async def add(self, episode: Dict[str, Any]) -> None:
        embedding = self.model.encode(json.dumps(episode))
        await self._check_connection()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO episodes (content, embedding) VALUES (?, ?)",
                (json.dumps(episode), embedding.tobytes())
            )
            await db.commit()
    
    async def get_similar(self, query_text: str, limit: int = 5) -> List[Dict]:
        query_vec = self.model.encode(query_text)
        async with aiosqlite.connect(self.db_path) as db:
            # Retrieve all episodes with precomputed embeddings (store as blob)
            async with db.execute("SELECT id, content, embedding FROM episodes") as cursor:
                rows = await cursor.fetchall()
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
