import asyncio
from collections import deque
from typing import Dict, Any, List, Optional
from datetime import datetime
from loguru import logger

class ShortTermMemory:
    """In-memory short-term storage with TTL."""
    
    def __init__(self, max_size: int = 100, ttl_seconds: int = 3600):
        self._store: deque = deque(maxlen=max_size)
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
    
    async def initialize(self) -> None:
        logger.info("ShortTermMemory initialized")
    
    async def add(self, item: Dict[str, Any]) -> None:
        async with self._lock:
            item["_timestamp"] = datetime.utcnow().timestamp()
            self._store.append(item)
    
    async def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        async with self._lock:
            now = datetime.utcnow().timestamp()
            valid = [i for i in self._store if now - i.get("_timestamp", 0) <= self._ttl]
            return list(valid)[-limit:]
    
    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
    
    async def shutdown(self) -> None:
        await self.clear()