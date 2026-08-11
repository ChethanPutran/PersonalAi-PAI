import chromadb
from chromadb.utils import embedding_functions
from typing import List, Dict, Any
from loguru import logger

class VectorStore:
    """ChromaDB-based semantic search."""
    
    def __init__(self, persist_dir: str = "./data/chroma"):
        self.persist_dir = persist_dir
        self.client = None
        self.collection = None
        self.embed_fn = None
    
    async def initialize(self) -> None:
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        self.embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        self.collection = self.client.get_or_create_collection(
            name="pai_memories",
            embedding_function=self.embed_fn
        )
        logger.info("VectorStore initialized")
    
    async def add_document(self, text: str, metadata: Dict[str, Any]) -> None:
        import uuid
        doc_id = str(uuid.uuid4())
        self.collection.add(
            documents=[text],
            metadatas=[metadata],
            ids=[doc_id]
        )
    
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        results = self.collection.query(query_texts=[query], n_results=limit)

        if not results or not results.get("documents"):
            return []
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        return [{"text": doc, "metadata": meta} for doc, meta in zip(documents, metadatas)]
    
    async def shutdown(self) -> None:
        # Chroma client handles persistence automatically
        pass