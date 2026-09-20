from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from pai.memory.episodic import EpisodicMemory
from pai.memory.knowledge_graph import KnowledgeGraph
from pai.memory.long_term import LongTermMemory
from pai.memory.procedural import ProceduralMemory
from pai.memory.short_term import ShortTermMemory
from pai.memory.vector_store import VectorStore


class MemoryManager:
    """
    Central memory orchestration layer.

    MemoryManager decides:
    - where information should be stored
    - which memory systems should be queried
    - how context is assembled

    Individual memory implementations own their persistence
    and retrieval mechanisms.
    """

    def __init__(
        self,
        *,
        user_id: str = "default",
        short_term_size: int = 100,
        short_term_ttl: int = 3600,
        long_term_db_path: str = "./data/long_term.db",
        episodic_db_path: str = "./data/episodic.db",
        vector_store_path: str = "./data/chroma",
        procedural_path: str = "./data/procedural.json",
        knowledge_graph_uri: str = "bolt://localhost:7687",
        knowledge_graph_user: str = "neo4j",
        knowledge_graph_password: str = "neo4j",
        embedding_model: str = "all-MiniLM-L6-v2",
    ):
        self.user_id = user_id

        self.short_term = ShortTermMemory(
            max_size=short_term_size,
            ttl_seconds=short_term_ttl,
        )

        self.long_term = LongTermMemory(
            db_path=long_term_db_path,
        )

        self.episodic = EpisodicMemory(
            db_path=episodic_db_path,
        )

        self.vector_store = VectorStore(
            persistence_path=vector_store_path,
            embedding_model=embedding_model,
        )

        self.procedural = ProceduralMemory(
            path=procedural_path,
        )

        self.knowledge_graph = KnowledgeGraph(
            uri=knowledge_graph_uri,
            user=knowledge_graph_user,
            password=knowledge_graph_password,
        )

        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return

        await self.short_term.initialize()
        await self.long_term.initialize()
        await self.episodic.initialize()
        await self.vector_store.initialize()
        await self.procedural.initialize()
        await self.knowledge_graph.initialize()

        self._initialized = True

        logger.info(
            "MemoryManager initialized"
        )

    async def remember_interaction(
        self,
        query: str,
        response: Any,
        *,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Store an interaction as working + episodic memory.

        It is intentionally NOT stored as long-term memory
        automatically.
        """

        content = {
            "query": query,
            "response": response,
        }

        await self.short_term.add(
            content,
            user_id=self.user_id,
            session_id=session_id,
            metadata=metadata,
        )

        episode = await self.episodic.add(
            content,
            user_id=self.user_id,
            session_id=session_id,
            metadata=metadata,
        )

        # Index the episode for semantic retrieval.
        await self.vector_store.add_document(
            text=f"User: {query}\nAssistant: {response}",
            metadata={
                "memory_type": "episodic",
                "memory_id": episode.id,
                "user_id": self.user_id,
                "session_id": session_id,
            },
            document_id=episode.id,
        )

    async def remember_preference(
        self,
        key: str,
        value: Any,
        *,
        importance: float = 0.8,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:

        record = await self.long_term.set_preference(
            key,
            value,
            user_id=self.user_id,
            importance=importance,
            metadata=metadata,
        )

        # Index persistent memory for semantic retrieval.
        await self.vector_store.add_document(
            text=f"{key}: {value}",
            metadata={
                "memory_type": "long_term",
                "memory_id": record.id,
                "user_id": self.user_id,
            },
            document_id=record.id,
        )

    async def remember_procedure(
        self,
        goal_pattern: str,
        steps: List[Dict[str, Any]],
        *,
        success: bool = True,
    ) -> None:

        await self.procedural.add_workflow(
            goal_pattern,
            steps,
            success=success,
        )

    async def recall(
        self,
        query: str,
        *,
        limit: int = 10,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retrieve context from relevant memory systems.
        """

        short_term = await self.short_term.get_recent(
            limit=limit,
            session_id=session_id,
        )

        episodic = await self.episodic.search_text(
            query,
            limit=limit,
            user_id=self.user_id,
        )

        long_term = await self.long_term.get_relevant(
            query,
            limit=limit,
            user_id=self.user_id,
        )

        semantic = await self.vector_store.search(
            query,
            limit=limit,
            where={
                "user_id": self.user_id,
            },
        )

        procedure = await self.procedural.get_workflow(
            query
        )

        return {
            "short_term": short_term,
            "long_term": long_term,
            "episodic": episodic,
            "semantic": semantic,
            "procedural": procedure,
        }

    async def get_preference(
        self,
        key: str,
        default: Any = None,
    ) -> Any:

        return await self.long_term.get_preference(
            key,
            default,
            user_id=self.user_id,
        )

    async def clear_session(
        self,
        session_id: Optional[str] = None,
    ) -> None:

        await self.short_term.clear(
            session_id=session_id
        )

    async def consolidate(self) -> None:
        """
        Run memory maintenance tasks.
        """

        await self.long_term.consolidate()

        logger.info(
            "Memory consolidation completed"
        )

    async def shutdown(self) -> None:
        if not self._initialized:
            return

        await self.short_term.shutdown()
        await self.long_term.shutdown()
        await self.episodic.shutdown()
        await self.vector_store.shutdown()
        await self.procedural.shutdown()
        await self.knowledge_graph.close()

        self._initialized = False

        logger.info(
            "MemoryManager shutdown"
        )