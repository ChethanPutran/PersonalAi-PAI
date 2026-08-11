"""Enhanced Memory System: ChromaDB vector store + Neo4j knowledge graph integration."""

import asyncio
import logging
import json
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict, field
from enum import Enum
import hashlib

logger = logging.getLogger(__name__)


class MemoryType(Enum):
    """Types of memory in the system."""
    EPISODIC = "episodic"  # Events, experiences
    SEMANTIC = "semantic"  # Facts, knowledge
    PROCEDURAL = "procedural"  # Skills, how-to
    CONTEXTUAL = "contextual"  # Current context


@dataclass
class MemoryEntry:
    """A single memory entry."""
    id: str
    memory_type: MemoryType
    content: str
    metadata: Dict[str, Any]
    importance: float  # 0.0-1.0
    timestamp: str
    tags: List[str] = field(default_factory=list)
    relationships: List[str] = field(default_factory=list)  # IDs of related memories
    embeddings: Optional[List[float]] = None


@dataclass
class SemanticTriple:
    """Knowledge graph triple: subject-predicate-object."""
    subject: str
    predicate: str
    object: str
    confidence: float
    source_memory_id: str


class ChromaDBService:
    """Handles vector storage and similarity search using ChromaDB."""
    
    def __init__(self, persist_dir: str = "./chromadb_data"):
        """Initialize ChromaDB service.
        
        Args:
            persist_dir: Directory for persistent ChromaDB storage
        """
        self.persist_dir = persist_dir
        self.client = None
        self.collections: Dict[str, Any] = {}
        
    async def initialize(self) -> None:
        """Initialize ChromaDB client."""
        try:
            import chromadb
            self.client = chromadb.PersistentClient(path=self.persist_dir)
            
            # Create collections for each memory type
            for mem_type in MemoryType:
                collection = self.client.get_or_create_collection(
                    name=mem_type.value,
                    metadata={"hnsw:space": "cosine"}
                )
                self.collections[mem_type.value] = collection
            
            logger.info("ChromaDB service initialized")
        except ImportError:
            logger.error("ChromaDB not installed")
            raise
    
    async def store_embedding(self, memory: MemoryEntry) -> None:
        """Store memory embedding in ChromaDB.
        
        Args:
            memory: Memory entry with embeddings
        """
        if memory.embeddings is None:
            logger.warning(f"No embeddings for memory {memory.id}")
            return
        
        collection = self.collections.get(memory.memory_type.value)
        if not collection:
            logger.error(f"No collection for memory type {memory.memory_type}")
            return
        
        collection.add(
            ids=[memory.id],
            embeddings=[memory.embeddings],
            documents=[memory.content],
            metadatas=[{
                "importance": memory.importance,
                "timestamp": memory.timestamp,
                "tags": json.dumps(memory.tags)
            }]
        )
        logger.debug(f"Stored embedding for memory {memory.id}")
    
    async def search_similar(self, query_embedding: List[float], 
                            memory_type: MemoryType,
                            n_results: int = 10,
                            importance_threshold: float = 0.0) -> List[Tuple[str, float]]:
        """Search for similar memories.
        
        Args:
            query_embedding: Query embedding vector
            memory_type: Type of memory to search
            n_results: Number of results to return
            importance_threshold: Minimum importance score
            
        Returns:
            List of (memory_id, similarity_score) tuples
        """
        collection = self.collections.get(memory_type.value)
        if not collection:
            return []
        
        try:
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results
            )
            
            # Filter by importance
            filtered = []
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i]
                similarity = 1 / (1 + distance)  # Convert distance to similarity
                filtered.append((doc_id, similarity))
            
            return sorted(filtered, key=lambda x: x[1], reverse=True)
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    async def search_by_text(self, query: str, memory_type: MemoryType,
                            n_results: int = 10) -> List[Tuple[str, float, str]]:
        """Search memories by text query.
        
        Args:
            query: Text query
            memory_type: Type of memory to search
            n_results: Number of results
            
        Returns:
            List of (memory_id, similarity, content) tuples
        """
        collection = self.collections.get(memory_type.value)
        if not collection:
            return []
        
        try:
            results = collection.query(
                query_texts=[query],
                n_results=n_results
            )
            
            output = []
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i]
                similarity = 1 / (1 + distance)
                content = results["documents"][0][i]
                output.append((doc_id, similarity, content))
            
            return sorted(output, key=lambda x: x[1], reverse=True)
        except Exception as e:
            logger.error(f"Text search failed: {e}")
            return []


class Neo4jService:
    """Handles knowledge graph operations using Neo4j."""
    
    def __init__(self, uri: str = "bolt://localhost:7687", 
                 username: str = "neo4j", password: str = "password"):
        """Initialize Neo4j service.
        
        Args:
            uri: Neo4j connection URI
            username: Neo4j username
            password: Neo4j password
        """
        self.uri = uri
        self.username = username
        self.password = password
        self.driver = None
    
    async def initialize(self) -> None:
        """Initialize Neo4j connection."""
        try:
            from neo4j import AsyncGraphDatabase
            self.driver = AsyncGraphDatabase.driver(self.uri, auth=(self.username, self.password))
            
            # Test connection
            async with self.driver.session() as session:
                await session.run("RETURN 1")
            
            logger.info("Neo4j service initialized")
        except ImportError:
            logger.error("neo4j driver not installed")
            raise
        except Exception as e:
            logger.error(f"Neo4j connection failed: {e}")
            raise
    
    async def create_triple(self, triple: SemanticTriple) -> bool:
        """Create a semantic triple in the knowledge graph.
        
        Args:
            triple: Semantic triple to store
            
        Returns:
            Success status
        """
        if not self.driver:
            logger.error("Neo4j driver not initialized")
            return False
        
        try:
            async with self.driver.session() as session:
                await session.run(
                    """
                    MERGE (s:Entity {name: $subject})
                    MERGE (o:Entity {name: $object})
                    MERGE (s)-[r:RELATION {
                        type: $predicate,
                        confidence: $confidence,
                        source: $source
                    }]->(o)
                    """,
                    subject=triple.subject,
                    object=triple.object,
                    predicate=triple.predicate,
                    confidence=triple.confidence,
                    source=triple.source_memory_id
                )
            return True
        except Exception as e:
            logger.error(f"Failed to create triple: {e}")
            return False
    
    async def query_relationships(self, entity: str, max_hops: int = 2) -> List[Dict[str, Any]]:
        """Query relationships for an entity.
        
        Args:
            entity: Entity name to query
            max_hops: Maximum relationship hops
            
        Returns:
            List of related entities and relationships
        """
        if not self.driver:
            return []
        
        try:
            async with self.driver.session() as session:
                results = await session.run(
                    f"""
                    MATCH (s:Entity {{name: $entity}})-[r*1..{max_hops}]-(connected)
                    RETURN connected.name as entity, TYPE(r[0]) as relation_type, r[0].confidence as confidence
                    LIMIT 50
                    """,
                    entity=entity
                )
                
                records = []
                async for record in results:
                    records.append({
                        "entity": record["entity"],
                        "relation_type": record["relation_type"],
                        "confidence": record["confidence"]
                    })
                
                return records
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return []


class EnhancedMemoryService:
    """Complete memory service combining vector DB and knowledge graph."""
    
    def __init__(self, chromadb_dir: str = "./chromadb_data",
                 neo4j_uri: str = "bolt://localhost:7687"):
        """Initialize enhanced memory service.
        
        Args:
            chromadb_dir: ChromaDB persistence directory
            neo4j_uri: Neo4j connection URI
        """
        self.vector_db = ChromaDBService(chromadb_dir)
        self.knowledge_graph = Neo4jService(neo4j_uri)
        self.memories: Dict[str, MemoryEntry] = {}
        self.embedder = None
    
    async def initialize(self) -> None:
        """Initialize all memory systems."""
        try:
            # Initialize vector DB
            await self.vector_db.initialize()
            
            # Initialize knowledge graph (may fail if Neo4j not running)
            try:
                await self.knowledge_graph.initialize()
            except Exception as e:
                logger.warning(f"Neo4j initialization skipped: {e}")
            
            # Initialize embedder for vector generation
            try:
                from sentence_transformers import SentenceTransformer
                self.embedder = SentenceTransformer('all-MiniLM-L6-v2')
                logger.info("Sentence embedder initialized")
            except ImportError:
                logger.error("sentence-transformers not installed")
                raise
            
            logger.info("Enhanced memory service initialized")
        except Exception as e:
            logger.error(f"Memory service initialization failed: {e}")
            raise
    
    async def store_memory(self, content: str, memory_type: MemoryType,
                          tags: Optional[List[str]] = None,
                          importance: float = 0.5,
                          metadata: Optional[Dict[str, Any]] = None) -> str:
        """Store a new memory entry.
        
        Args:
            content: Memory content
            memory_type: Type of memory
            tags: Tags for the memory
            importance: Importance score (0.0-1.0)
            metadata: Additional metadata
            
        Returns:
            Memory ID
        """
        # Generate memory ID
        memory_id = hashlib.md5(content.encode()).hexdigest()[:16]
        
        # Generate embeddings
        embeddings = self.embedder.encode(content).tolist()
        
        # Create memory entry
        memory = MemoryEntry(
            id=memory_id,
            memory_type=memory_type,
            content=content,
            metadata=metadata or {},
            importance=importance,
            timestamp=datetime.utcnow().isoformat(),
            tags=tags or []
        )
        memory.embeddings = embeddings
        
        # Store in vector DB
        await self.vector_db.store_embedding(memory)
        
        # Store in local cache
        self.memories[memory_id] = memory
        
        logger.info(f"Stored memory {memory_id}: {content[:50]}...")
        return memory_id
    
    async def search_memories(self, query: str, memory_types: Optional[List[MemoryType]] = None,
                             top_k: int = 10) -> List[MemoryEntry]:
        """Search memories by query.
        
        Args:
            query: Search query
            memory_types: Types to search (all if None)
            top_k: Number of results
            
        Returns:
            List of matching memories
        """
        if memory_types is None:
            memory_types = list(MemoryType)
        
        all_results = []
        
        for mem_type in memory_types:
            results = await self.vector_db.search_by_text(query, mem_type, top_k)
            all_results.extend([(mem_id, score, mem_type) for mem_id, score, _ in results])
        
        # Sort by score and return
        all_results.sort(key=lambda x: x[1], reverse=True)
        
        memories = []
        for mem_id, _, _ in all_results[:top_k]:
            if mem_id in self.memories:
                memories.append(self.memories[mem_id])
        
        return memories
    
    async def get_context(self, query: str, context_type: str = "general") -> str:
        """Get contextual information for a query.
        
        Args:
            query: Current query/context
            context_type: Type of context needed
            
        Returns:
            Contextual information
        """
        # Search relevant memories
        relevant = await self.search_memories(query, top_k=5)
        
        if not relevant:
            return f"No relevant context found for: {query}"
        
        # Format context
        context_parts = [f"Relevant memories for '{query}':"]
        for mem in relevant:
            context_parts.append(f"- [{mem.memory_type.value}] {mem.content}")
        
        return "\n".join(context_parts)
    
    async def create_semantic_connection(self, subject: str, predicate: str, 
                                         object: str, confidence: float = 0.8) -> bool:
        """Create a semantic relationship in knowledge graph.
        
        Args:
            subject: Subject entity
            predicate: Relationship type
            object: Object entity
            confidence: Confidence score
            
        Returns:
            Success status
        """
        if not self.knowledge_graph.driver:
            return False
        
        triple = SemanticTriple(
            subject=subject,
            predicate=predicate,
            object=object,
            confidence=confidence,
            source_memory_id="system"
        )
        
        return await self.knowledge_graph.create_triple(triple)
    
    async def get_entity_context(self, entity: str) -> Dict[str, Any]:
        """Get complete context for an entity from knowledge graph.
        
        Args:
            entity: Entity name
            
        Returns:
            Entity context with relationships
        """
        relationships = await self.knowledge_graph.query_relationships(entity)
        
        return {
            "entity": entity,
            "relationships": relationships,
            "relationship_count": len(relationships)
        }
    
    async def clear_old_memories(self, days: int = 30, memory_type: Optional[MemoryType] = None) -> int:
        """Clear old memories (archival).
        
        Args:
            days: Keep memories newer than this
            memory_type: Specific type to clear (all if None)
            
        Returns:
            Number of memories cleared
        """
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cleared = 0
        
        for mem_id, memory in list(self.memories.items()):
            if memory_type and memory.memory_type != memory_type:
                continue
            if memory.timestamp < cutoff_date and memory.importance < 0.3:
                del self.memories[mem_id]
                cleared += 1
        
        logger.info(f"Cleared {cleared} old memories")
        return cleared
