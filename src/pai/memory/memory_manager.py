"""Memory Management System."""

from hashlib import md5
from typing import Dict, Any, List, Optional
from datetime import datetime
from loguru import logger

from pai.memory.short_term import ShortTermMemory
from pai.memory.long_term import LongTermMemory
from pai.memory.episodic import EpisodicMemory
from pai.memory.vector_store import VectorStore
from pai.memory.knowledge_graph import KnowledgeGraph
from pai.memory.procedural import ProceduralMemory


class MemoryManager:
    """
    Central memory management system.

    Manages different memory types:
    - Short-term: Current session context
    - Long-term: Persistent knowledge and preferences
    - Episodic: Past interactions and experiences
    - Vector: Semantic search capabilities
    """

    def __init__(self,
                 long_term_db_url: str,
                 episodic_db_url: str,
                 vector_store_path: str,
                 procedural_path: str,
                 knowledge_graph_uri: str,
                 knowledge_graph_user: str,
                 knowledge_graph_password: str,
                 user_id: Optional[str] = None,
                 short_term_size: int = 100,
                 short_term_ttl: int = 3600,  # 1 hour TTL
                 embedding_model: str = "all-MiniLM-L6-v2",
                 ):
        self.short_term = ShortTermMemory(
            max_size=short_term_size, ttl_seconds=short_term_ttl
        )
        self.long_term = LongTermMemory(
            db_url=long_term_db_url
        )
        self.episodic = EpisodicMemory(
            db_url=episodic_db_url
        )
        self.vector_store = VectorStore(
            persistence_path=vector_store_path, embedding_model=embedding_model)
        self.knowledge_graph = KnowledgeGraph(
            uri=knowledge_graph_uri,
            user=knowledge_graph_user,
            password=knowledge_graph_password
        )
        self.procedural = ProceduralMemory(
            path=procedural_path
        )
        self.user_id: Optional[str] = user_id

        self._initialized = False

    async def initialize(self) -> None:
        """Initialize all memory systems."""
        await self.short_term.initialize()
        await self.long_term.initialize()
        await self.episodic.initialize()
        await self.vector_store.initialize()
        await self.knowledge_graph.initialize()
        await self.procedural.initialize()

        self._initialized = True
        logger.info("Memory manager initialized")

    async def store_interaction(self, query: str, response: Dict[str, Any]) -> None:
        """Store a user interaction in memory."""
        assert self.user_id is not None, "User ID must be set before storing interactions"

        # Store in short-term
        await self.short_term.add({
            "query": query,
            "response": response,
            "timestamp": datetime.now().timestamp()
        })

        # Store in episodic memory
        await self.episodic.add({
            "query": query,
            "response": response,
            "timestamp": datetime.now().timestamp()
        })

        # Store in vector store for semantic search
        await self.vector_store.add_document(
            text=query,
            metadata={"response": str(response)}
        )

        await self.long_term.store({
            "goal": query,
            "query": query,
            "response": response,
            "timestamp": datetime.now().timestamp()
        })

        goal_hash = md5(query.encode()).hexdigest()

        # Add relationships automatically
        await self.knowledge_graph.add_entity("User", self.user_id)
        await self.knowledge_graph.add_relationship("User", self.user_id, "PERFORMED", "Goal", goal_hash)

    async def retrieve_context(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Retrieve relevant context from all memory systems."""
        context = {
            "short_term": await self.short_term.get_recent(limit),
            "long_term": await self.long_term.get_relevant(query),
            "episodic": await self.episodic.get_similar(query, limit),
            "semantic": await self.vector_store.search(query, limit)
        }

        return context

    async def store_preference(self, key: str, value: Any) -> None:
        """Store a user preference."""
        await self.long_term.set_preference(key, value)

    async def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a user preference."""
        return await self.long_term.get_preference(key, default)

    async def remember(self, memory_type: str, data: Dict[str, Any]) -> None:
        """Store a specific memory."""
        if memory_type == "short_term":
            await self.short_term.add(data)
        elif memory_type == "long_term":
            await self.long_term.store(data)
        elif memory_type == "episodic":
            await self.episodic.add(data)
        else:
            logger.warning(f"Unknown memory type: {memory_type}")

    async def recall(self, memory_type: str, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Recall memories of a specific type."""
        if memory_type == "short_term":
            return await self.short_term.get_recent(query.get("limit", 10))
        elif memory_type == "long_term":
            return await self.long_term.retrieve(query)
        elif memory_type == "episodic":
            return await self.episodic.get_similar(query.get("text", ""))
        else:
            logger.warning(f"Unknown memory type: {memory_type}")
            return []

    async def clear_session(self) -> None:
        """Clear short-term memory for the current session."""
        await self.short_term.clear()
        logger.info("Session memory cleared")

    async def shutdown(self) -> None:
        """Shutdown all memory systems."""
        await self.short_term.shutdown()
        await self.long_term.shutdown()
        await self.episodic.shutdown()
        await self.vector_store.shutdown()
        await self.knowledge_graph.close()
        await self.procedural.shutdown()
