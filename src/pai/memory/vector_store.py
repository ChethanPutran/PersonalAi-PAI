from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from loguru import logger

from pai.memory.utils import _matches_where
from pai.observability.tracer import get_tracer


class VectorStore:
    """
    Semantic vector retrieval infrastructure.

    Does not decide what constitutes a memory — only indexes and retrieves
    text. Backend is ChromaDB if available, in-memory word-overlap fallback
    otherwise.
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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "vector_store.initialize",
            collection=self.collection_name,
            persistence_path=self.persistence_path,
            embedding_model=self.embedding_model,
        ) as span:
            t0 = time.perf_counter()

            try:
                self.client = chromadb.PersistentClient(
                    path=self.persistence_path,
                    settings=Settings(anonymized_telemetry=False),
                )

                embedding_fn_class = getattr(
                    embedding_functions,
                    "SentenceTransformerEmbeddingFunction",
                    None,
                )
                if embedding_fn_class:
                    self.embed_fn = embedding_fn_class(
                        model_name=self.embedding_model,
                    )
                else:
                    self.embed_fn = embedding_functions.DefaultEmbeddingFunction()

                self.collection = self.client.get_or_create_collection(
                    name=self.collection_name,
                    embedding_function=self.embed_fn,
                )

                elapsed_ms = (time.perf_counter() - t0) * 1000
                span.set_attribute("backend", "chroma")
                span.set_attribute(
                    "initialized_ms", round(elapsed_ms, 1),
                )
                span.set_attribute(
                    "document_count",
                    self.collection.count() if hasattr(self.collection, "count") else None,
                )

                logger.info(
                    "VectorStore initialized: {} ({} docs, {:.0f}ms)",
                    self.collection_name,
                    self.collection.count() if hasattr(self.collection, "count") else "?",
                    elapsed_ms,
                )

            except Exception as exc:
                # Graceful degradation — do NOT propagate. This is a
                # supported fallback, not a failure.
                self.client = None
                self.collection = None
                self.embed_fn = None

                span.set_attribute("backend", "fallback")
                span.set_attribute(
                    "fallback_reason", str(exc)[:500],
                )

                logger.warning(
                    "VectorStore unavailable; using in-memory fallback: {}", exc,
                )

        # ------------------------------------------------------------------
    # Batch delete
    # ------------------------------------------------------------------

    async def delete_many(self, ids: List[str]) -> int:
        """
        Delete documents by id. Returns the number of ids attempted
        (Chroma doesn't return a count, so we report the input size).

        Callers that need the actual deleted count should subtract
        `count()` before/after. This is fine for the sweep use case,
        where every candidate id came from a fresh `search` call.
        """
        if not ids:
            return 0

        tracer = get_tracer()
        async with tracer.span(
            "vector_store.delete_many",
            id_count=len(ids),
            backend=(
                "chroma" if self.collection is not None else "fallback"
            ),
        ) as span:

            if self.collection is not None:
                try:
                    await asyncio.to_thread(
                        self.collection.delete, ids=list(ids),
                    )
                    span.set_attribute("deleted", len(ids))
                    return len(ids)
                except Exception as exc:
                    span.set_error(f"{type(exc).__name__}: {exc}")
                    return 0

            # Fallback path
            before = len(self._fallback_documents)
            id_set = set(ids)
            self._fallback_documents = [
                d for d in self._fallback_documents
                if d["id"] not in id_set
            ]
            removed = before - len(self._fallback_documents)
            span.set_attribute("deleted", removed)
            return removed

    async def delete_by_metadata(
        self,
        where: Dict[str, Any],
    ) -> int:
        """
        Delete documents matching a metadata filter.

        Uses Chroma's native `where` support when available (O(1) scan
        on the backend); falls back to a Python filter over the in-memory
        corpus otherwise.

        Returns the number of documents removed. Chroma doesn't report
        counts, so on the Chroma path we measure the delta in `count()`
        before/after — accurate but incurs one extra round-trip. If you
        don't need the count, use `delete_by_metadata_count=None`…
        actually just use this; the extra count() is cheap.

        Use `delete_many(ids)` instead when you already have a precise
        list of ids — it's a single backend call with no count overhead.
        """
        if not where:
            return 0

        tracer = get_tracer()
        async with tracer.span(
            "vector_store.delete_by_metadata",
            where_keys=sorted(where.keys()),
            backend=(
                "chroma" if self.collection is not None else "fallback"
            ),
        ) as span:

            if self.collection is not None:
                try:
                    before = self.collection.count()
                    await asyncio.to_thread(
                        self.collection.delete, where=where,
                    )
                    after = self.collection.count()
                    removed = max(0, before - after)
                    span.set_attribute("deleted", removed)
                    return removed
                except Exception as exc:
                    span.set_error(f"{type(exc).__name__}: {exc}")
                    return 0

            # Fallback path — evaluate where-clause in Python
            before = len(self._fallback_documents)
            self._fallback_documents = [
                d for d in self._fallback_documents
                if not _matches_where(
                    d.get("metadata") or {}, where,
                )
            ]
            removed = before - len(self._fallback_documents)
            span.set_attribute("deleted", removed)
            return removed

    # ------------------------------------------------------------------
    # Read helpers (non-semantic)
    # ------------------------------------------------------------------

    async def get_by_metadata(
        self,
        where: Dict[str, Any],
        limit: int = 100,
        *,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Fetch documents by metadata filter without running a semantic
        search. Used for maintenance tasks (sweeps, audits, exports)
        where the query is a filter, not a natural-language string.

        Returned shape matches `search`: {id, text, metadata, distance}
        with `distance` always None.
        """
        tracer = get_tracer()
        async with tracer.span(
            "vector_store.get_by_metadata",
            where_keys=sorted(where.keys()),
            limit=limit,
            backend=(
                "chroma" if self.collection is not None else "fallback"
            ),
        ) as span:

            if self.collection is not None:
                try:
                    results = await asyncio.to_thread(
                        self.collection.get,
                        where=where or None,
                        limit=limit,
                        offset=offset,
                        include=["documents", "metadatas"],
                    )
                except Exception as exc:
                    span.set_error(f"{type(exc).__name__}: {exc}")
                    return []

                documents = results.get("documents") or []
                metadatas = results.get("metadatas") or []
                ids = results.get("ids") or []

                out = [
                    {
                        "id": ids[i],
                        "text": documents[i],
                        "metadata": metadatas[i] or {},
                        "distance": None,
                    }
                    for i in range(len(ids))
                ]
                span.set_attribute("result_count", len(out))
                return out

            # Fallback path
            filtered = [
                d for d in self._fallback_documents
                if _matches_where(d.get("metadata") or {}, where)
            ]
            page = filtered[offset: offset + limit]
            out = [
                {**d, "distance": None} for d in page
            ]
            span.set_attribute("result_count", len(out))
            return out

    async def count(
        self,
        *,
        where: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Number of documents (optionally filtered by `where`)."""
        if self.collection is not None:
            try:
                if where:
                    # Chroma's count() doesn't accept where in older
                    # versions. Get with a large limit and count.
                    results = await asyncio.to_thread(
                        self.collection.get,
                        where=where,
                        include=[],
                    )
                    return len(results.get("ids") or [])
                return await asyncio.to_thread(self.collection.count)
            except Exception as exc:
                logger.warning("vector_store.count failed: {}", exc)
                return 0
        if where:
            return sum(
                1 for d in self._fallback_documents
                if _matches_where(d.get("metadata") or {}, where)
            )
        return len(self._fallback_documents)

    async def clear(self) -> int:
        """
        Delete every document. Used in tests and after a schema change
        when old entries are known to be invalid.

        Returns the number of documents removed.
        """
        removed = await self.count()
        if self.collection is not None:
            try:
                await asyncio.to_thread(self.collection.delete, ids=None)
                logger.info("VectorStore.clear: removed {} doc(s)", removed)
                return removed
            except Exception as exc:
                logger.warning("VectorStore.clear failed: {}", exc)
                return 0
        self._fallback_documents.clear()
        logger.info("VectorStore.clear: removed {} doc(s)", removed)
        return removed


    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

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

        tracer = get_tracer()
        async with tracer.span(
            "vector_store.add_document",
            document_id=document_id,
            text_length=len(text),
            backend=(
                "chroma" if self.collection is not None else "fallback"
            ),
        ) as span:
            t0 = time.perf_counter()

            if self.collection is None:
                self._fallback_documents.append(
                    {
                        "id": document_id,
                        "text": text,
                        "metadata": metadata,
                    }
                )
                span.set_attribute(
                    "fallback_size", len(self._fallback_documents),
                )
                return document_id

            try:
                await asyncio.to_thread(
                    self.collection.add,
                    documents=[text],
                    metadatas=[metadata],
                    ids=[document_id],
                )
                span.set_attribute(
                    "duration_ms",
                    round((time.perf_counter() - t0) * 1000, 1),
                )
                return document_id
            except Exception as exc:
                span.set_error(f"{type(exc).__name__}: {exc}")
                raise

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        limit: int = 5,
        *,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not query:
            return []

        tracer = get_tracer()
        async with tracer.span(
            "vector_store.search",
            query_length=len(query),
            limit=limit,
            has_where=bool(where),
            backend=(
                "chroma" if self.collection is not None else "fallback"
            ),
        ) as span:

            if self.collection is None:
                results = self._fallback_search(query, limit,where=where,)
                span.set_attribute("result_count", len(results))
                span.set_attribute(
                    "fallback_corpus_size", len(self._fallback_documents),
                )
                return results

            t0 = time.perf_counter()
            try:
                results = await asyncio.to_thread(
                    self.collection.query,
                    query_texts=[query],
                    n_results=limit,
                    where=where,
                )
            except Exception as exc:
                span.set_error(f"{type(exc).__name__}: {exc}")
                return []

            duration_ms = (time.perf_counter() - t0) * 1000
            span.set_attribute("duration_ms", round(duration_ms, 1))

            documents = results.get("documents") or [[]]
            metadatas = results.get("metadatas") or [[]]
            ids = results.get("ids") or [[]]
            distances = results.get("distances") or [[]]

            output = []
            for index, document in enumerate(documents[0]):
                distance = None
                if distances and distances[0]:
                    distance = distances[0][index]
                output.append(
                    {
                        "id": ids[0][index],
                        "text": document,
                        "metadata": metadatas[0][index],
                        "distance": distance,
                    }
                )

            span.set_attribute("result_count", len(output))
            if output and output[0].get("distance") is not None:
                span.set_attribute(
                    "top_distance", round(output[0]["distance"], 3),
                )

            return output

    def _fallback_search(
        self,
        query: str,
        limit: int,
        *,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        # Intentionally NOT traced — called from `search` which already
        # has a span.
        query_words = set(query.lower().split())
        scored = []
        for document in self._fallback_documents:
            if where and not _matches_where(
                document.get("metadata") or {}, where,
            ):
                continue
            words = set(document["text"].lower().split())
            score = len(query_words & words)
            scored.append((score, document))

        scored.sort(key=lambda item: item[0], reverse=True)

        return [
            document
            for score, document in scored[:limit]
            if score > 0
        ]

    # ------------------------------------------------------------------
    # Delete / shutdown
    # ------------------------------------------------------------------

    async def delete(self, document_id: str) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "vector_store.delete",
            document_id=document_id,
        ) as span:
            if self.collection is not None:
                try:
                    await asyncio.to_thread(
                        self.collection.delete,
                        ids=[document_id],
                    )
                    span.set_attribute("deleted", True)
                except Exception as exc:
                    span.set_error(f"{type(exc).__name__}: {exc}")
                    raise
            else:
                before = len(self._fallback_documents)
                self._fallback_documents = [
                    d for d in self._fallback_documents
                    if d["id"] != document_id
                ]
                span.set_attribute(
                    "deleted", len(self._fallback_documents) < before,
                )

    async def shutdown(self) -> None:
        self.client = None
        self.collection = None
        self.embed_fn = None
        logger.info("VectorStore shutdown")