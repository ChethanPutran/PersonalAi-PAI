import chromadb
from chromadb.utils import embedding_functions
from typing import List, Dict, Any, cast
from chromadb.api.types import EmbeddingFunction
from chromadb.config import Settings
from loguru import logger

class VectorStore:
    """ChromaDB-based semantic search."""
    
    def __init__(self, persistence_path: str = "./data/chroma", embedding_model: str = "all-MiniLM-L6-v2"):
        self.persistence_path = persistence_path
        self.embedding_model = embedding_model
        self.client = None
        self.collection = None
        self.embed_fn = None
    
    async def initialize(self) -> None:
        try:
            self.client = chromadb.PersistentClient(
                path=self.persistence_path,
                settings=Settings(anonymized_telemetry=False),
            )
            sentence_transformer_fn = getattr(
                embedding_functions,
                "SentenceTransformerEmbeddingFunction",
                None,
            )
            if sentence_transformer_fn is not None:
                self.embed_fn = sentence_transformer_fn(model_name=self.embedding_model)
            else:
                self.embed_fn = embedding_functions.DefaultEmbeddingFunction()
            self.collection = self.client.get_or_create_collection(
                name="pai_memories",
                embedding_function=cast(EmbeddingFunction, self.embed_fn)
            )
            logger.info("VectorStore initialized")
        except Exception as exc:
            self.client = None
            self.collection = None
            self.embed_fn = None
            logger.warning(f"VectorStore disabled because embeddings could not be loaded: {exc}")
    
    async def add_document(self, text: str, metadata: Dict[str, Any]) -> None:
        if self.collection is None:
            return

        import uuid
        doc_id = str(uuid.uuid4())
        self.collection.add(
            documents=[text],
            metadatas=[metadata],
            ids=[doc_id]
        )
    
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        if self.collection is None:
            return []

        results = self.collection.query(query_texts=[query], n_results=limit)

        if not results or not results.get("documents"):
            return []
        documents = results.get("documents") or [[]]
        metadatas = results.get("metadatas") or [[]]
        return [{"text": doc, "metadata": meta} for doc, meta in zip(documents[0] if documents else [], metadatas[0] if metadatas else [])]
    
    async def shutdown(self) -> None:
        # Chroma client handles persistence automatically
        pass
