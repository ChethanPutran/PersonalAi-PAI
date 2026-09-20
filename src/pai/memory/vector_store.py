from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from loguru import logger


class VectorStore:
    """
    Semantic vector retrieval infrastructure.

    VectorStore does not decide what constitutes a memory.
    It only indexes and retrieves text.
    """

    def __init__(
        self,
        persistence_path: str = "./data/chroma",
        collection_name: str = "pai_memories",
        embedding_model: str = "all-MiniLM-L6-v2",
    ):
        self.persistence_path = persistence_path
        self.collection_name = collection_name
        self.embedding_model = embedding_model

        self.client = None
        self.collection = None
        self.embed_fn = None

        self._fallback_documents: List[Dict[str, Any]] = []

    async def initialize(self) -> None:
        try:
            self.client = chromadb.PersistentClient(
                path=self.persistence_path,
                settings=Settings(
                    anonymized_telemetry=False
                ),
            )

            embedding_fn_class = getattr(
                embedding_functions,
                "SentenceTransformerEmbeddingFunction",
                None,
            )

            if embedding_fn_class:
                self.embed_fn = embedding_fn_class(
                    model_name=self.embedding_model
                )
            else:
                self.embed_fn = (
                    embedding_functions.DefaultEmbeddingFunction()
                )

            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embed_fn,
            )

            logger.info(
                "VectorStore initialized: {}",
                self.collection_name,
            )

        except Exception as exc:
            self.client = None
            self.collection = None
            self.embed_fn = None

            logger.warning(
                "VectorStore unavailable; using in-memory fallback: {}",
                exc,
            )

    async def add_document(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        document_id: Optional[str] = None,
    ) -> str:

        if not text:
            raise ValueError("Document text cannot be empty")

        document_id = document_id or str(uuid.uuid4())

        metadata = metadata or {}

        if self.collection is None:
            self._fallback_documents.append(
                {
                    "id": document_id,
                    "text": text,
                    "metadata": metadata,
                }
            )

            return document_id

        await asyncio.to_thread(
            self.collection.add,
            documents=[text],
            metadatas=[metadata],
            ids=[document_id],
        )

        return document_id

    async def search(
        self,
        query: str,
        limit: int = 5,
        *,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:

        if not query:
            return []

        if self.collection is None:
            return self._fallback_search(
                query,
                limit,
            )

        results = await asyncio.to_thread(
            self.collection.query,
            query_texts=[query],
            n_results=limit,
            where=where,
        )

        documents = results.get("documents") or [[]]
        metadatas = results.get("metadatas") or [[]]
        ids = results.get("ids") or [[]]
        distances = results.get("distances") or [[]]

        output = []

        for index, document in enumerate(documents[0]):
            output.append(
                {
                    "id": ids[0][index],
                    "text": document,
                    "metadata": metadatas[0][index],
                    "distance": (
                        distances[0][index]
                        if distances and distances[0]
                        else None
                    ),
                }
            )

        return output

    def _fallback_search(
        self,
        query: str,
        limit: int,
    ) -> List[Dict[str, Any]]:

        query_words = set(
            query.lower().split()
        )

        scored = []

        for document in self._fallback_documents:
            words = set(
                document["text"].lower().split()
            )

            score = len(query_words & words)

            scored.append(
                (
                    score,
                    document,
                )
            )

        scored.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        return [
            document
            for score, document in scored[:limit]
            if score > 0
        ]

    async def delete(self, document_id: str) -> None:
        if self.collection is not None:
            await asyncio.to_thread(
                self.collection.delete,
                ids=[document_id],
            )

    async def shutdown(self) -> None:
        self.client = None
        self.collection = None
        self.embed_fn = None

        logger.info("VectorStore shutdown")