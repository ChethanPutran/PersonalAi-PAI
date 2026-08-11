"""
Memory System - Long-term and semantic memory management
"""
import logging
import uuid
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    """Types of memory"""
    EPISODIC = "episodic"  # Events and experiences
    SEMANTIC = "semantic"  # Facts and knowledge
    PROCEDURAL = "procedural"  # Skills and procedures


@dataclass
class Memory:
    """Memory structure"""
    id: str
    user_id: str
    memory_type: MemoryType
    content: str
    importance: int  # 1-10
    tags: List[str]
    embedding: Optional[List[float]] = None
    created_at: datetime = None
    last_accessed: datetime = None
    metadata: Dict[str, Any] = None


class MemoryService:
    """
    Manages long-term and semantic memory for the system.
    
    Responsibilities:
    - Store episodic, semantic, and procedural memories
    - Retrieve relevant memories
    - Manage memory embeddings for similarity search
    - Memory persistence and retrieval
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize memory service"""
        self.config = config or {}
        self.memories: Dict[str, List[Memory]] = {}  # user_id -> memories
        self.vector_store = None
        logger.info("Memory Service initialized")
    
    async def initialize(self, vector_store=None):
        """Initialize memory service"""
        self.vector_store = vector_store
        logger.info("Memory Service dependencies initialized")
    
    async def store(self,
                   user_id: str,
                   content: str,
                   memory_type: MemoryType = MemoryType.EPISODIC,
                   importance: int = 5,
                   tags: List[str] = None,
                   embedding: Optional[List[float]] = None,
                   metadata: Dict[str, Any] = None) -> str:
        """Store a memory"""
        
        memory_id = str(uuid.uuid4())
        
        memory = Memory(
            id=memory_id,
            user_id=user_id,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=tags or [],
            embedding=embedding,
            created_at=datetime.utcnow(),
            last_accessed=datetime.utcnow(),
            metadata=metadata or {}
        )
        
        if user_id not in self.memories:
            self.memories[user_id] = []
        
        self.memories[user_id].append(memory)
        logger.info(f"Memory stored: {memory_id} for user {user_id}")
        
        return memory_id
    
    async def retrieve(self, memory_id: str) -> Optional[Memory]:
        """Retrieve a specific memory"""
        for user_memories in self.memories.values():
            for memory in user_memories:
                if memory.id == memory_id:
                    memory.last_accessed = datetime.utcnow()
                    return memory
        return None
    
    async def search(self,
                    user_id: str,
                    query: str,
                    limit: int = 5,
                    memory_type: Optional[MemoryType] = None) -> List[Memory]:
        """Search memories by query"""
        
        if user_id not in self.memories:
            return []
        
        results = []
        for memory in self.memories[user_id]:
            # Filter by type if specified
            if memory_type and memory.memory_type != memory_type:
                continue
            
            # Simple text matching (in production, use semantic search)
            if query.lower() in memory.content.lower():
                memory.last_accessed = datetime.utcnow()
                results.append(memory)
        
        # Sort by importance and recency
        results.sort(
            key=lambda m: (m.importance, m.last_accessed),
            reverse=True
        )
        
        return results[:limit]
    
    async def search_by_tags(self,
                            user_id: str,
                            tags: List[str],
                            limit: int = 5) -> List[Memory]:
        """Search memories by tags"""
        
        if user_id not in self.memories:
            return []
        
        results = []
        for memory in self.memories[user_id]:
            if any(tag in memory.tags for tag in tags):
                results.append(memory)
        
        results.sort(
            key=lambda m: (m.importance, m.last_accessed),
            reverse=True
        )
        
        return results[:limit]
    
    async def get_recent_memories(self,
                                  user_id: str,
                                  limit: int = 10,
                                  memory_type: Optional[MemoryType] = None) -> List[Memory]:
        """Get recent memories"""
        
        if user_id not in self.memories:
            return []
        
        memories = self.memories[user_id]
        
        if memory_type:
            memories = [m for m in memories if m.memory_type == memory_type]
        
        # Sort by creation date, most recent first
        memories.sort(key=lambda m: m.created_at, reverse=True)
        
        return memories[:limit]
    
    async def update_memory(self, memory_id: str, **kwargs) -> bool:
        """Update a memory"""
        
        for user_memories in self.memories.values():
            for memory in user_memories:
                if memory.id == memory_id:
                    for key, value in kwargs.items():
                        if hasattr(memory, key):
                            setattr(memory, key, value)
                    memory.last_accessed = datetime.utcnow()
                    logger.info(f"Memory updated: {memory_id}")
                    return True
        
        logger.warning(f"Memory not found: {memory_id}")
        return False
    
    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory"""
        
        for user_id, user_memories in self.memories.items():
            for i, memory in enumerate(user_memories):
                if memory.id == memory_id:
                    user_memories.pop(i)
                    logger.info(f"Memory deleted: {memory_id}")
                    return True
        
        logger.warning(f"Memory not found: {memory_id}")
        return False
    
    async def get_user_summary(self, user_id: str) -> Dict[str, Any]:
        """Get user memory summary"""
        
        if user_id not in self.memories:
            return {
                "user_id": user_id,
                "total_memories": 0,
                "by_type": {}
            }
        
        user_memories = self.memories[user_id]
        
        by_type = {}
        for memory_type in MemoryType:
            count = sum(1 for m in user_memories if m.memory_type == memory_type)
            by_type[memory_type.value] = count
        
        return {
            "user_id": user_id,
            "total_memories": len(user_memories),
            "by_type": by_type,
            "avg_importance": sum(m.importance for m in user_memories) / len(user_memories) if user_memories else 0
        }
    
    async def export_memories(self, user_id: str) -> List[Dict[str, Any]]:
        """Export user memories"""
        
        if user_id not in self.memories:
            return []
        
        export = []
        for memory in self.memories[user_id]:
            export.append({
                "id": memory.id,
                "type": memory.memory_type.value,
                "content": memory.content,
                "importance": memory.importance,
                "tags": memory.tags,
                "created_at": memory.created_at.isoformat(),
                "last_accessed": memory.last_accessed.isoformat()
            })
        
        return export
