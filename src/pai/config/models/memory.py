from enum import Enum, StrEnum

from pydantic import BaseModel, Field


class ShortTermBackend(StrEnum):
    MEMORY = "memory"
    REDIS = "redis"
    SQLITE = "sqlite"

class ShortTermMemoryConfig(BaseModel):
    """Short-term memory configuration."""
    enabled: bool = Field(default=True)
    ttl_seconds: int = Field(default=3600)  # seconds
    size: int = Field(default=100)
    backend: ShortTermBackend = Field(default=ShortTermBackend.MEMORY)

class LongTermBackend(StrEnum):
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MONGODB = "mongodb"

class LongTermMemoryConfig(BaseModel):
    """Long-term memory configuration."""
    enabled: bool = Field(default=True)
    backend: LongTermBackend = Field(default=LongTermBackend.SQLITE)
    db_url: str = Field(default="./data/long_term.db")

class EpisodicMemoryConfig(BaseModel):
    """Episodic memory configuration."""
    enabled: bool = Field(default=True)
    backend: LongTermBackend = Field(default=LongTermBackend.SQLITE)
    db_url: str = Field(default="./data/episodic.db")   

class VectorStoreProvider(StrEnum):
    """Enum-like class for vector store providers."""
    CHROMA = "chroma"
    FAISS = "faiss"
    MILVUS = "milvus"

class EmbeddingModelProvider(StrEnum):
    """Enum-like class for embedding model providers."""
    OPENAI = "openai"
    COHERE = "cohere"
    HUGGINGFACE = "huggingface"
    SENTENCE_TRANSFORMERS = "sentence_transformers"


class EmbeddingConfig(BaseModel):
    """Embedding configuration."""
    provider: EmbeddingModelProvider = Field(default=EmbeddingModelProvider.SENTENCE_TRANSFORMERS) 
    model_name: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")
    device: str = Field(default="auto")  # Options: "cpu", "cuda", "auto"
    max_tokens: int = Field(default=8191)
    batch_size: int = Field(default=100)

class VectorStoreConfig(BaseModel):
    """Vector store configuration."""
    enabled: bool = Field(default=True)
    backend: VectorStoreProvider = Field(default=VectorStoreProvider.CHROMA) 
    persist_directory: str = Field(default="./data/chroma")
    collection_name: str = Field(default="pai_memory")
    embeddings: EmbeddingConfig = EmbeddingConfig()



class ProceduralMemoryConfig(BaseModel):
    """Procedural memory configuration."""
    path: str = Field(default="./data/procedural.json")


class KnowledgeGraphBackend(StrEnum):
    """Enum-like class for knowledge graph backends."""
    NEO4J = "neo4j"
    ARANGO = "arango"
    JANUSGRAPH = "janusgraph"
    ORIENTDB = "orientdb"
    TIGERGRAPH = "tigergraph"

class KnowledgeGraphConfig(BaseModel):
    """Knowledge graph configuration."""
    enabled: bool = Field(default=True)
    backend: KnowledgeGraphBackend = Field(default=KnowledgeGraphBackend.NEO4J)
    uri: str | None = Field(default=None)
    database: str | None = Field(default=None)
    username: str | None = Field(default=None)
    password: str | None = Field(default=None)

class MemorySafetyMode(StrEnum):
    """What to do with a write that looks like a poisoning attempt."""
    OFF = "off"                   # no checks (tests / debugging only)
    WARN = "warn"                 # log + event, store as-is
    QUARANTINE = "quarantine"     # store sanitized + flagged, never recalled or indexed
    REJECT = "reject"             # do not store at all


class MemorySafetyConfig(BaseModel):
    """Memory-poisoning defenses (see pai/memory/safety)."""
    mode: MemorySafetyMode = MemorySafetyMode.QUARANTINE
    # A write whose risk score is at or above this is treated as an attack.
    risk_threshold: float = Field(default=0.6, ge=0.0, le=1.0)

    # Size limits (characters)
    max_interaction_chars: int = Field(default=4000, ge=100)
    max_preference_value_chars: int = Field(default=500, ge=10)
    max_recall_chars_per_item: int = Field(default=400, ge=50)

    # Flood / quota protection (per user)
    writes_per_minute: int = Field(default=120, ge=1)
    max_episodes_per_user: int = Field(default=5000, ge=10)
    max_preferences_per_user: int = Field(default=200, ge=1)

    # Retention
    episodic_retention_days: int = Field(default=180, ge=1)
    consolidate_every_n_writes: int = Field(default=50, ge=1)

    # Learn facts from what the USER says (never from tool/assistant text)
    extraction_enabled: bool = True
    # Inferred (not explicitly "remember ...") facts are capped at this importance.
    inferred_importance_cap: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryConfig(BaseModel):
    """Memory system configuration."""
    short_term: ShortTermMemoryConfig = ShortTermMemoryConfig()
    long_term: LongTermMemoryConfig = LongTermMemoryConfig()
    episodic: EpisodicMemoryConfig = EpisodicMemoryConfig()
    vector_store: VectorStoreConfig = VectorStoreConfig()
    procedural: ProceduralMemoryConfig = ProceduralMemoryConfig()   
    knowledge_graph: KnowledgeGraphConfig = KnowledgeGraphConfig()
    safety: MemorySafetyConfig = MemorySafetyConfig()