from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from loguru import logger
import re
from typing import Iterable
from datetime import datetime, timezone
from pai.event_bus.bus import EventBus
from pai.memory.episodic import EpisodicMemory
from pai.memory.knowledge_graph import KnowledgeGraph
from pai.memory.long_term import LongTermMemory
from pai.memory.procedural import ProceduralMemory
from pai.memory.short_term import ShortTermMemory
from pai.memory.vector_store import VectorStore
from pai.observability.tracer import get_tracer
from pai.common.manager import Manager

def _flatten_content(content: Any) -> str:
    """
    Convert whatever the memory backend returns as `text`/`content` into
    a plain string suitable for prompt injection and pattern matching.

    Handles:
      - str       → returned as-is
      - dict      → formatted as "User: ...\nAssistant: ..." for the
                    {"query", "response"} shape used by
                    MemoryManager.remember_interaction; falls back to
                    joining all values for unknown shapes
      - list/tuple → flattened and joined with spaces
      - None/other → empty string

    This is what makes `text[:240]` safe and `_is_stale_given_capabilities`
    actually see the memory body. Before this existed, episodic results
    came through as dicts and the filter was silently a no-op.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        q = content.get("query") or content.get("user") or ""
        r = (
            content.get("response")
            or content.get("assistant")
            or content.get("reply")
            or ""
        )
        if q or r:
            parts = []
            if q:
                parts.append(f"User: {q}")
            if r:
                parts.append(f"Assistant: {r}")
            return "\n".join(parts)
        # Unknown dict shape — join values so at least the body is visible.
        return " ".join(str(v) for v in content.values() if v)
    if isinstance(content, (list, tuple)):
        return " ".join(_flatten_content(c) for c in content if c)
    return str(content)


def _reltime(ts: Any) -> str:
    """
    Human-friendly relative time for prompt injection.

    Accepts:
      - a Unix timestamp (int / float)
      - a ``datetime`` (naive assumed UTC)
      - an ISO-8601 string (as returned by JSON columns)

    Returns "unknown" for anything unparseable or falsy — better to
    surface a neutral label than to crash the recall path.
    """
    if ts is None or ts == "" or ts == 0:
        return "unknown"

    # datetime → epoch
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        epoch = ts.timestamp()
    # ISO-8601 string → epoch
    elif isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return "unknown"
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        epoch = parsed.timestamp()
    # int / float → epoch
    else:
        try:
            epoch = float(ts)
        except (TypeError, ValueError):
            return "unknown"

    delta = time.time() - epoch

    # Clock skew / future timestamps: don't render "-3 min ago".
    if delta < 0:
        return "just now"
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)} min ago"
    if delta < 86400:
        return f"{int(delta / 3600)} h ago"
    if delta < 604800:
        return f"{int(delta / 86400)} days ago"
    return f"{int(delta / 604800)} weeks ago"



# ---------------------------------------------------------------------------
# Stale-claim detection
# ---------------------------------------------------------------------------
#
# These heuristics exist because the LLM sometimes answers
#   "I don't have a camera"
# in a turn that *succeeds* (status=completed). Those replies are the ones
# that poison later recalls. A failed plan is caught by the `failed` flag;
# a *successful* reply that said "I can't X" is caught here.

_NEGATIVE_CLAIM_PATTERNS = [
    re.compile(r"\bi\s+(?:don't|do\s+not)\s+have\b", re.I),
    re.compile(r"\bi\s+(?:can't|cannot|can\s+not)\b", re.I),
    re.compile(r"\bi(?:'m| am)\s+(?:not\s+able|unable)\b", re.I),
    re.compile(r"\bi(?:'m| am)\s+just\s+(?:an?\s+)?(?:ai|language\s+model|chatbot)\b", re.I),
    re.compile(r"\bwithout\s+(?:a\s+|the\s+)?(?:camera|body|microphone|screen|browser)\b", re.I),
]

# Capability id ↔ keywords that, when present in a memory, indicate the
# memory is *about* that capability. Must be specific — "camera.record"
# should not match a mention of "photo".
_CAPABILITY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("camera.capture",   ("take a photo", "take a picture", "snap a", "selfie",
                          "photo", "picture", "capture image")),
    ("camera.record",    ("record video", "take a video", "record a video",
                          "video", "film", "movie")),
    ("camera.stream",    ("live preview", "camera stream", "webcam preview")),
    ("audio.record",     ("record audio", "record voice", "microphone")),
    ("screen.capture",   ("screenshot", "screen capture")),
    ("browser.navigate", ("browse", "open a website", "navigate to", "web page")),
    ("clipboard.read",   ("clipboard",)),
    ("clipboard.write",  ("clipboard",)),
    ("notification.send", ("notification", "notify")),
]


def _has_negative_claim(text: str) -> bool:
    return any(p.search(text) for p in _NEGATIVE_CLAIM_PATTERNS)


def _referenced_capability_ids(text: str) -> set[str]:
    """Capability ids the text appears to be *about*."""
    lower = text.lower()
    return {
        cap_id
        for cap_id, kws in _CAPABILITY_KEYWORDS
        if any(kw in lower for kw in kws)
    }


def _is_stale_given_capabilities(
    text: str,
    current_capabilities: set[str],
) -> bool:
    """
    True iff `text` makes a negative claim about a capability that is
    currently available.

    Example: "I can't take a photo" → stale when `camera.capture`
    is present in `current_capabilities`.

    Example: "I can't record video" → NOT stale when only `camera.capture`
    (no `camera.record`) is available; the claim is still true.
    """
    if not _has_negative_claim(text):
        return False
    refs = _referenced_capability_ids(text)
    return bool(refs & current_capabilities)


def _doc_fields(doc: Any) -> tuple[str, Any, Dict[str, Any]]:
    """
    Normalize a memory entry to (text, ts, metadata).

    `text` is always a ``str`` — safe to slice, regex-match, and inject
    into a prompt. `ts` may be a float epoch, a ``datetime``, or an
    ISO-8601 string; ``_reltime`` handles all three.
    """
    if isinstance(doc, dict):
        raw = doc.get("text") or doc.get("content") or ""
        ts = doc.get("created_at") or doc.get("timestamp") or 0
        meta = doc.get("metadata") or {}
    else:
        raw = getattr(doc, "text", "") or getattr(doc, "content", "")
        ts = getattr(doc, "created_at", 0)
        meta = getattr(doc, "metadata", {}) or {}

    return _flatten_content(raw), ts, meta


# Retry intent — the user is asking to redo / continue / explain the
# previous attempt.
_RETRY_PATTERNS = [
    re.compile(r"\bretry\b", re.I),
    re.compile(r"\btry\s+again\b", re.I),
    re.compile(r"\bagain\b", re.I),
    re.compile(r"\bredo\b", re.I),
    re.compile(r"\brepeat\b", re.I),
    re.compile(r"\bdo\s+(?:it|that)\s+again\b", re.I),
    re.compile(r"\bcontinue\b", re.I),
    re.compile(r"\bsame\b", re.I),
    re.compile(r"\bwhy\s+did\s+(?:it|that|the\s+last\s+\w+)\s+fail\b", re.I),
    re.compile(r"\bwhat\s+happened\b", re.I),
    re.compile(r"\b(?:didn'?t|did\s+not)\s+work\b", re.I),
]

def _looks_like_retry(query: str) -> bool:
    return any(p.search(query) for p in _RETRY_PATTERNS)


def _summary_of_failure(raw: Any) -> str:
    """Pull concrete error lines out of a stored raw_result blob."""
    if not isinstance(raw, dict):
        return ""
    lines: List[str] = []
    for r in raw.get("results") or []:
        if not isinstance(r, dict):
            continue
        if r.get("status") == "completed":
            continue
        cap = r.get("capability") or r.get("type") or "task"
        err = str(r.get("error") or "").strip()
        lines.append(f"  • {cap}: {err[:200] or '(no message)'}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Result summarization (used by store_interaction to shorten task output)
# ---------------------------------------------------------------------------
def _summarize_result(result: Any) -> str:
    if result is None:
        return "(no result)"
    if isinstance(result, str):
        return result[:1000]
    if not isinstance(result, dict):
        return str(result)[:1000]

    is_failure = result.get("status") == "failed"

    # For failures: preserve both the chat reply AND the concrete error
    # lines so a subsequent retry can see *why* the previous attempt
    # didn't work.
    if is_failure:
        err_lines: List[str] = []
        for r in result.get("results") or []:
            if not isinstance(r, dict):
                continue
            if r.get("status") == "completed":
                continue
            cap = r.get("capability") or r.get("type") or "task"
            err = str(r.get("error") or "").strip()
            err_lines.append(f"{cap}: {err[:300] or 'failed'}")

        reply_text = ""
        for r in result.get("results") or []:
            if isinstance(r, dict) and r.get("reply"):
                reply_text = str(r["reply"])[:600]
                break

        parts: List[str] = []
        if err_lines:
            parts.append("Errors: " + " | ".join(err_lines))
        if reply_text:
            parts.append("Assistant said: " + reply_text)
        if parts:
            return "\n".join(parts)[:2000]

    # ---- existing happy-path logic (chat reply first) ----
    for r in result.get("results", []) or []:
        reply = r.get("reply") if isinstance(r, dict) else None
        if reply:
            return str(reply)[:1000]

    lines: List[str] = []
    for r in result.get("results", []) or []:
        if not isinstance(r, dict):
            continue
        cap = r.get("capability") or r.get("type") or "task"
        status = r.get("status", "completed")
        if status == "completed":
            res = r.get("result") or {}
            bits = []
            if isinstance(res, dict):
                for k in ("path", "url", "title", "value", "ok"):
                    if res.get(k):
                        bits.append(f"{k}={res[k]}")
            lines.append(f"{cap}: ok" + (f" ({', '.join(bits)})" if bits else ""))
        else:
            lines.append(f"{cap}: {status}" + (f" — {r.get('error', '')[:200]}" if r.get("error") else ""))

    if not lines:
        return "(empty result)"
    return "; ".join(lines)[:1000]


class MemoryManager(Manager):
    """
    Central memory orchestration layer.

    MemoryManager decides:
      - where information should be stored
      - which memory systems should be queried
      - how context is assembled

    Individual memory implementations own their own persistence and
    retrieval; this class coordinates them.
    """
    name = "memory_manager"

    def __init__(
        self,
        *,
        event_bus: EventBus,
        user_id:str,
        knowledge_graph_enabled: bool = True,
         knowledge_graph_uri: Optional[str] = None,
                knowledge_graph_user: Optional[str] = None,
                knowledge_graph_password: Optional[str] = None ,
        short_term_size: int = 100,
        short_term_ttl: int = 3600,
        long_term_db_url: str = "./data/long_term.db",
        episodic_db_url: str = "./data/episodic.db",
        vector_store_enabled: bool = True,
        vector_store_path: str = "./data/chroma",
        procedural_path: str = "./data/procedural.json",
        embedding_model: str = "all-MiniLM-L6-v2",
    ):
        super().__init__(event_bus=event_bus)
        self.user_id = user_id

        self.short_term = ShortTermMemory(
            max_size=short_term_size,
            ttl_seconds=short_term_ttl,
        )
        self.long_term = LongTermMemory(db_path=long_term_db_url)
        self.episodic = EpisodicMemory(db_path=episodic_db_url)
        self.vector_store_enabled = vector_store_enabled
        self.knowledge_graph_enabled = knowledge_graph_enabled
        if vector_store_enabled:
            self.vector_store = VectorStore(
                persistence_path=vector_store_path,
                embedding_model=embedding_model,
            )
        else:
            logger.info("Vector store is disabled.")
            self.vector_store = None

        self.procedural = ProceduralMemory(path=procedural_path)

        if knowledge_graph_enabled:
            self.knowledge_graph = KnowledgeGraph(
                uri=knowledge_graph_uri,
                user=knowledge_graph_user,
                password=knowledge_graph_password,
            )
        else:
            logger.info("Knowledge store is disabled.")
            self.knowledge_graph = None

        self._initialized = False

    async def sweep_stale_capability_memories(
        self,
        capability_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> int:
        """
        Delete memories whose text makes a negative claim about
        `capability_id`, which is now available.

        Best-effort: returns 0 if the backend has no delete support or
        the search returns nothing.
        """
        user_id = user_id or self.user_id

        if not self.vector_store_enabled:
            logger.warning("Vector store is not enabled.")
            return 0

        # Search is the right primitive here because stale detection
        # needs the text (regex filters), and `search` is the one method
        # that returns text alongside metadata.
        try:
            candidates = await self.vector_store.search(
                capability_id,          # dummy query — we only want the docs
                limit=200,
                where={"user_id": {"$eq": user_id}},
            )
        except Exception as exc:
            logger.debug("sweep_stale search failed: {}", exc)
            return 0

        ids_to_delete: list[str] = []
        for doc in candidates:
            text, _ts, meta = _doc_fields(doc)
            if not text:
                continue
            refs = _referenced_capability_ids(text)
            if capability_id not in refs:
                continue
            if not _has_negative_claim(text):
                continue
            doc_id = meta.get("memory_id") or doc.get("id")
            if doc_id:
                ids_to_delete.append(doc_id)

        if not ids_to_delete:
            return 0

        try:
            return await self.vector_store.delete_many(ids_to_delete)
        except Exception as exc:
            logger.warning("sweep_stale delete_many failed: {}", exc)
            return 0
        
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def _on_initialize(self) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "memory.initialize",
            user_id=self.user_id,
        ) as span:

            initialized_ok: List[str] = []
            failed: List[Dict[str, str]] = []

            for name, backend in [
                ("short_term", self.short_term),
                ("long_term", self.long_term),
                ("episodic", self.episodic),
                ("vector_store", self.vector_store),
                ("procedural", self.procedural),
                ("knowledge_graph", self.knowledge_graph),
            ]:
                async with tracer.span(
                    f"memory.init.{name}",
                ) as one_span:
                    try:
                        await backend.initialize()
                        initialized_ok.append(name)
                    except Exception as exc:
                        logger.warning(
                            "Memory backend {} failed to initialize: {}",
                            name, exc,
                        )
                        one_span.set_error(f"{type(exc).__name__}: {exc}")
                        failed.append({"backend": name, "error": str(exc)})

            span.set_attribute("initialized", initialized_ok)
            span.set_attribute("failures", failed)

            logger.info(
                "MemoryManager initialized ({} backends up, {} down: {})",
                len(initialized_ok),
                len(failed),
                [f["backend"] for f in failed],
            )

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def _on_shutdown(self) -> None:
        tracer = get_tracer()
        async with tracer.span("memory.shutdown"):
            for name, closer in [
                ("short_term", self.short_term.shutdown),
                ("long_term", self.long_term.shutdown),
                ("episodic", self.episodic.shutdown),
                ("vector_store", self.vector_store.shutdown if self.vector_store_enabled else None),
                ("procedural", self.procedural.shutdown),
                ("knowledge_graph", self.knowledge_graph.close if self.knowledge_graph_enabled else None),
            ]:
                try:
                    if closer:
                        await closer()
                except Exception as exc:
                    logger.warning(
                        "Memory backend {} failed to shut down: {}",
                        name, exc,
                    )
            logger.info("MemoryManager shutdown")

    # ------------------------------------------------------------------
    # WRITE — interaction
    # ------------------------------------------------------------------

    async def remember_interaction(
        self,
        query: str,
        response: Any,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        success: bool = True,
        capabilities: Optional[Iterable[str]] = None,
    ) -> Optional[str]:
        """
        Store an interaction across:
          - short_term (session working memory)
          - episodic (durable per-user log)
          - vector_store (semantic index for future recall)

        `success=False` keeps the entry out of the vector store, so
        failed attempts never surface as semantic context. `capabilities`
        records the capability snapshot at write time (audit + drift
        detection).

        Returns the episode id, or None on total failure.
        """
        await self._ensure_initialized()

        user_id = user_id or self.user_id
        tracer = get_tracer()

        async with tracer.span(
            "memory.remember_interaction",
            user_id=user_id,
            query_length=len(query),
            has_session=bool(session_id),
            success=success,
        ) as span:

            meta = dict(metadata or {})
            if request_id:
                meta["request_id"] = request_id
            if capabilities is not None:
                meta["capability_snapshot"] = sorted(set(capabilities))
            if not success:
                meta["failed"] = True
                meta["exclude_from_recall"] = True

            content = {"query": query, "response": response}
            episode_id: Optional[str] = None
            

            # ---- 1. short-term ----
            async with tracer.span("memory.remember.short_term") as st_span:
                try:
                    await self.short_term.add(
                        content,
                        user_id=user_id,
                        session_id=session_id,
                        metadata=meta,
                    )
                except Exception as exc:
                    logger.warning("short_term add failed: {}", exc)
                    st_span.set_error(str(exc))

            # ---- 2. episodic ----
            async with tracer.span("memory.remember.episodic") as ep_span:
                try:
                    episode = await self.episodic.add(
                        content,
                        user_id=user_id,
                        session_id=session_id,
                        metadata=meta,
                    )
                    episode_id = getattr(episode, "id", None)
                    ep_span.set_attribute("episode_id", episode_id)
                except Exception as exc:
                    logger.warning("episodic add failed: {}", exc)
                    ep_span.set_error(str(exc))

            # ---- 3. vector ----
            # This is the single most important guard against memory
            # poisoning: a failed tool attempt must never be retrievable
            # as if it were the assistant's considered answer.
            
            if self.vector_store_enabled and  episode_id is not None and success:
                async with tracer.span(
                    "memory.remember.vector",
                    document_id=episode_id,
                ) as vec_span:
                    try:
                        response_text = (
                            response if isinstance(response, str)
                            else _summarize_result(response)
                        )
                        await self.vector_store.add_document(
                        text=f"User: {query}\nAssistant: {response_text}",
                        metadata={
                            "memory_type": "episodic",
                            "memory_id": episode_id,
                            "user_id": user_id,
                            "session_id": session_id or "",
                            "request_id": request_id or "",
                            # Chroma requires scalar metadata. Comma-join so it's still
                            # readable and splittable when we need it back.
                            "capability_snapshot": ",".join(meta.get("capability_snapshot") or []),
                        },
                        document_id=episode_id,
                        )
                    except Exception as exc:
                        logger.warning("vector_store add failed: {}", exc)
                        vec_span.set_error(str(exc))
            self._emit("memory.stored",
                       {
                        "user_id":user_id, 
                        "session_id":session_id, 
                        "request_id":request_id, 
                        "episode_id":episode_id, 
                        "success":success
            })
            span.set_attribute("episode_id", episode_id)
            span.set_attribute("indexed_for_recall", success)

            # Consolidate the memory if the memory size exceeds
            await self.consolidate()
            return episode_id

    # ------------------------------------------------------------------
    # Alias used by TaskOrchestrator._store_interaction
    # ------------------------------------------------------------------

    async def store_interaction(
        self,
        goal: str,
        result: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        success: bool = True,
        capabilities: Optional[Iterable[str]] = None,
    ) -> Optional[str]:
        """
        Orchestrator-friendly alias for remember_interaction.

        The result dict is summarized into a compact string before
        being stored, so recall queries don't pull megabytes of raw
        task JSON into the prompt.
        """
        summary = _summarize_result(result)
        return await self.remember_interaction(
            query=goal,
            response=summary,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            metadata={
                **(metadata or {}),
                "raw_result": result,
            },
            success=success,
            capabilities=capabilities,
        )

    # ------------------------------------------------------------------
    # WRITE — preference
    # ------------------------------------------------------------------

    async def remember_preference(
        self,
        key: str,
        value: Any,
        *,
        importance: float = 0.8,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._ensure_initialized()

        tracer = get_tracer()
        async with tracer.span(
            "memory.remember_preference",
            user_id=self.user_id,
            key=key,
        ) as span:
            try:
                record = await self.long_term.set_preference(
                    key,
                    value,
                    user_id=self.user_id,
                    importance=importance,
                    metadata=metadata,
                )
                span.set_attribute(
                    "record_id", getattr(record, "id", None),
                )
            except Exception as exc:
                logger.warning("long_term set_preference failed: {}", exc)
                span.set_error(str(exc))
                return

            if self.vector_store_enabled:
                async with tracer.span("memory.remember_preference.vector"):
                    try:
                        await self.vector_store.add_document(
                            text=f"{key}: {value}",
                            metadata={
                                "memory_type": "long_term",
                                "memory_id": getattr(record, "id", ""),
                                "user_id": self.user_id,
                            },
                            document_id=getattr(record, "id", key),
                        )
                    except Exception as exc:
                        logger.warning(
                            "vector_store add for preference failed: {}", exc,
                        )

    # ------------------------------------------------------------------
    # WRITE — procedure
    # ------------------------------------------------------------------

    async def remember_procedure(
        self,
        goal_pattern: str,
        steps: List[Dict[str, Any]],
        *,
        success: bool = True,
    ) -> None:
        await self._ensure_initialized()

        tracer = get_tracer()
        async with tracer.span(
            "memory.remember_procedure",
            goal_pattern=goal_pattern[:200],
            step_count=len(steps),
            success=success,
        ):
            try:
                await self.procedural.add_workflow(
                    goal_pattern,
                    steps,
                    success=success,
                )
            except Exception as exc:
                logger.warning(
                    "procedural add_workflow failed: {}", exc,
                )

    # ------------------------------------------------------------------
    # READ — the missing link
    # ------------------------------------------------------------------

    async def recall(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        limit: int = 10,
        session_id: Optional[str] = None,
        exclude_session: bool = False,
    ) -> Dict[str, Any]:
        """
        Retrieve context from every memory layer.

        Returned dict always contains all five keys (empty lists on
        failure) so callers can safely do `memories.get("episodic", [])`.
        """
        await self._ensure_initialized()

        user_id = user_id or self.user_id
        tracer = get_tracer()

        async with tracer.span(
            "memory.recall",
            user_id=user_id,
            query_length=len(query),
            limit=limit,
        ) as span:

            result: Dict[str, Any] = {
                "short_term": [],
                "long_term": [],
                "episodic": [],
                "semantic": [],
                "procedural": None,
            }

            # ---- 1. short_term ----
            async with tracer.span("memory.recall.short_term") as st_span:
                try:
                    result["short_term"] = await self.short_term.get_recent(
                        limit=limit,
                        session_id=session_id,
                    )
                    st_span.set_attribute(
                        "count", len(result["short_term"] or []),
                    )
                except Exception as exc:
                    logger.warning("short_term read failed: {}", exc)
                    st_span.set_error(str(exc))

            # ---- 2. long_term ----
            async with tracer.span("memory.recall.long_term") as lt_span:
                try:
                    result["long_term"] = await self.long_term.get_relevant(
                        query,
                        limit=limit,
                        user_id=user_id,
                    )
                    lt_span.set_attribute(
                        "count", len(result["long_term"] or []),
                    )
                except Exception as exc:
                    logger.warning("long_term read failed: {}", exc)
                    lt_span.set_error(str(exc))

            # ---- 3. episodic ----
            async with tracer.span("memory.recall.episodic") as ep_span:
                try:
                    result["episodic"] = await self.episodic.search_text(
                        query,
                        limit=limit,
                        user_id=user_id,
                    )
                    ep_span.set_attribute(
                        "count", len(result["episodic"] or []),
                    )
                except Exception as exc:
                    logger.warning("episodic read failed: {}", exc)
                    ep_span.set_error(str(exc))

            # ---- 4. semantic (vector) ----
            if self.vector_store_enabled:
                async with tracer.span("memory.recall.semantic") as vec_span:
                    try:
                        where = {"user_id": {"$eq": user_id}}

                        raw = await self.vector_store.search(
                            query,
                            limit=limit * 2 if exclude_session else limit,
                            where=where,
                        )

                        # Exclude current session in Python
                        if exclude_session and session_id:
                            raw = [
                                doc for doc in raw
                                if (doc.get("metadata") or {}).get("session_id") != session_id
                            ]
                        result["semantic"] = raw[:limit]
                        vec_span.set_attribute("count", len(result["semantic"]))
                    except Exception as exc:
                        logger.warning("vector_store read failed: {}", exc)
                        vec_span.set_error(str(exc))

            # ---- 5. procedural ----
            async with tracer.span("memory.recall.procedural") as proc_span:
                try:
                    result["procedural"] = await self.procedural.get_workflow(
                        query,
                    )
                    proc_span.set_attribute(
                        "found", result["procedural"] is not None,
                    )
                except Exception as exc:
                    logger.warning("procedural read failed: {}", exc)
                    proc_span.set_error(str(exc))

            counts = {
                                k: (
                                    len(v) if isinstance(v, list)
                                    else (1 if v else 0)
                                )
                                for k, v in result.items()
                            },
            
            span.set_attribute(
                "counts",counts
                
            )
            self._emit("memory.recalled",
                {
                    "user_id":user_id, 
                    "session_id":session_id, 
                    "query_len":len(query), 
                    "counts":counts
            })
            return result

    # ------------------------------------------------------------------
    # Convenience — format recall into a prompt-ready string
    # ------------------------------------------------------------------

    async def recall_for_prompt(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        limit: int = 5,
        session_id: Optional[str] = None,
        exclude_session: bool = True,
        current_capabilities: Optional[Iterable[str]] = None,
        include_failed: Optional[bool] = None,   # ← new
    ) -> str:
        """
        Recall and format memories for prompt injection.

        ``include_failed`` — if ``True``, failed attempts *are* surfaced,
        labelled ``(PREVIOUS ATTEMPT FAILED)`` so the LLM treats them as
        history, not as its considered answer. If ``None`` (default), the
        flag is auto-derived from ``query``: retry-shaped queries flip it on.
        """
        memories = await self.recall(
            query,
            user_id=user_id,
            limit=limit,
            session_id=session_id,
            exclude_session=exclude_session,
        )

        if include_failed is None:
            include_failed = _looks_like_retry(query)

        current_caps: set[str] = set(current_capabilities or ())
        sections: List[str] = []

        # ---- long-term preferences (unchanged) ----
        long_term = memories.get("long_term") or []
        if long_term:
            lines = []
            for item in long_term[:limit]:
                if isinstance(item, dict):
                    k = item.get("key") or item.get("name") or "?"
                    v = item.get("value")
                else:
                    k = getattr(item, "key", "?")
                    v = getattr(item, "value", None)
                lines.append(f"- {k}: {v}")
            if lines:
                sections.append("KNOWN USER PREFERENCES:\n" + "\n".join(lines))

        # ---- past interactions ----
        #
        # Merge semantic + episodic + (on retry) short_term. Each list can
        # contribute different entries — semantic has successful interactions,
        # episodic has everything that reached storage, short_term has the
        # current session's turns even when they haven't been indexed yet.
        #
        # Dedup on the text body so an interaction stored in both episodic
        # and short_term shows once.
        candidate_lists: list[list] = []
        if memories.get("semantic"):
            candidate_lists.append(memories["semantic"])
        if memories.get("episodic"):
            candidate_lists.append(memories["episodic"])
        if include_failed and memories.get("short_term"):
            # short_term is only for same-session context; when this is a
            # retry, the current session's previous turn is the most
            # important signal we have.
            candidate_lists.append(memories["short_term"])

        past_lines: List[str] = []
        seen_bodies: set[str] = set()
        skipped_failed = 0
        skipped_stale = 0
        skipped_dup = 0

        for bucket in candidate_lists:
            for doc in bucket:
                text, ts, meta = _doc_fields(doc)
                if not text:
                    continue

                key = str(text)[:200]
                if key in seen_bodies:
                    skipped_dup += 1
                    continue
                seen_bodies.add(key)

                is_failed = bool(
                    meta.get("failed") or meta.get("exclude_from_recall")
                )

                # Legacy string markers (pre-`success` flag data)
                if not is_failed:
                    if 'status": "failed"' in text or "': failed" in text:
                        is_failed = True

                if is_failed and not include_failed:
                    skipped_failed += 1
                    continue

                if current_caps and _is_stale_given_capabilities(text, current_caps):
                    skipped_stale += 1
                    continue

                # For failures, append the concrete error blob that
                # `store_interaction` attached as `raw_result` metadata.
                err_block = ""
                if is_failed:
                    err_block = _summary_of_failure(meta.get("raw_result"))
                    if err_block:
                        err_block = "\n" + err_block

                if is_failed:
                    past_lines.append(
                        f"- {_reltime(ts)} (PREVIOUS ATTEMPT FAILED): "
                        f"{str(text)[:240]}{err_block}"
                    )
                else:
                    past_lines.append(f"- {_reltime(ts)}: {str(text)[:240]}")

                if len(past_lines) >= limit * 2 if include_failed else limit:
                    break
            if past_lines and len(past_lines) >= (limit * 2 if include_failed else limit):
                break

        if past_lines:
            header = "RELEVANT PAST INTERACTIONS:"
            if include_failed:
                header += (
                    " (a PREVIOUS ATTEMPT FAILED line means the last try was "
                    "made and did not succeed; use it as context for the "
                    "current request, not as a reason to refuse)"
                )
            sections.append(header + "\n" + "\n".join(past_lines))

        if skipped_failed or skipped_stale or skipped_dup:
            logger.debug(
                "memory.recall_for_prompt: filtered {} failed, {} stale, "
                "{} dup; include_failed={}, capabilities={}",
                skipped_failed, skipped_stale, skipped_dup,
                include_failed, sorted(current_caps),
            )

        # ---- procedural (unchanged) ----
        procedure = memories.get("procedural")
        if procedure:
            goal = (
                procedure.get("goal_pattern")
                if isinstance(procedure, dict)
                else getattr(procedure, "goal_pattern", None)
            )
            steps = (
                procedure.get("steps")
                if isinstance(procedure, dict)
                else getattr(procedure, "steps", [])
            )
            if goal:
                sections.append(
                    f"KNOWN PROCEDURE for '{goal}':\n"
                    f"- {len(steps or [])} steps recorded"
                )

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Preference read
    # ------------------------------------------------------------------

    async def get_preference(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        await self._ensure_initialized()

        tracer = get_tracer()
        async with tracer.span(
            "memory.get_preference",
            user_id=self.user_id,
            key=key,
        ):
            try:
                return await self.long_term.get_preference(
                    key,
                    default,
                    user_id=self.user_id,
                )
            except Exception as exc:
                logger.warning("get_preference failed: {}", exc)
                return default

    # ------------------------------------------------------------------
    # Session / maintenance
    # ------------------------------------------------------------------

    async def clear_session(
        self,
        session_id: Optional[str] = None,
    ) -> None:
        await self._ensure_initialized()

        tracer = get_tracer()
        async with tracer.span(
            "memory.clear_session",
            session_id=session_id,
        ):
            try:
                await self.short_term.clear(session_id=session_id)
            except Exception as exc:
                logger.warning("clear_session failed: {}", exc)

    async def consolidate(self) -> None:
        await self._ensure_initialized()

        tracer = get_tracer()
        async with tracer.span("memory.consolidate"):
            try:
                removed = await self.long_term.consolidate()
                self._emit("memory.consolidated",
                           {
                               
                               "removed":removed
                                
                           })
                logger.info("Memory consolidation completed")

            except Exception as exc:
                logger.warning("consolidate failed: {}", exc)
