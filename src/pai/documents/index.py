"""The user's document index: text chunks the user's DEVICES extracted from files they allowed.

Design rules
  * Devices extract and chunk; the backend only stores, embeds and searches (raw files never leave the device).
  * Per user, tagged with the device and path. A device can only write/remove its own chunks.
  * The BOUNDARY is enforced at query time too, from the device's CURRENT plugin settings (allowed / denied
    folders), so a stale chunk of a folder the user later removed can never be returned.
  * Chunk text is untrusted data: it is only ever shown to the model inside a boundary-wrapped block
    (see files.search) and never becomes memory, a preference or an instruction.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from pai.plugins.config_schema import path_policy

MAX_CHUNK_CHARS = 2000
MAX_CHUNKS_PER_FILE = 2000
MAX_CHUNKS_PER_REQUEST = 500
MAX_CHUNKS_PER_USER = 200_000
FILES_PLUGIN_IDS = ("files", "files_android")      # Linux and Android ids of the files plugin


class IndexError_(ValueError):
    """A request the index refuses (too big, over quota, bad shape)."""


@dataclass
class Chunk:
    text: str
    page: Optional[int] = None


@dataclass
class IndexedFile:
    path: str
    mtime: float = 0.0
    size: int = 0
    chunks: List[Chunk] = field(default_factory=list)


def _norm(p: str) -> str:
    return str(Path(p).expanduser().resolve(strict=False))


def _inside(path: Path, roots: Iterable[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def allowed_by_policy(path: str, policy: Mapping[str, Any]) -> bool:
    """Is this file inside the device's current allowed folders and outside every denied one?"""
    target = Path(path).expanduser().resolve(strict=False)
    denied = [Path(d).expanduser().resolve(strict=False) for d in policy.get("denied") or []]
    if _inside(target, denied):
        # a more specific allowed root inside a denied folder wins (same rule as the file plugins)
        allowed = [Path(a).expanduser().resolve(strict=False) for a in policy.get("allowed") or []]
        specific = [a for a in allowed if any(d == a or d in a.parents for d in denied)]
        return _inside(target, specific)
    allowed = [Path(a).expanduser().resolve(strict=False) for a in policy.get("allowed") or []]
    return bool(allowed) and _inside(target, allowed)


def chunk_id(user_id: str, device_id: str, path: str, ordinal: int) -> str:
    return hashlib.sha1(f"{user_id}\x00{device_id}\x00{path}\x00{ordinal}".encode()).hexdigest()


class DocumentIndex:
    def __init__(self, store: Any) -> None:
        self._store = store                        # a VectorStore on its own collection
        self._ready = False

    @property
    def store(self) -> Any:
        return self._store

    async def ready(self) -> None:
        """Open the collection on first use (the embedding model itself loads later still, on first embed)."""
        if not self._ready:
            self._ready = True
            if getattr(self._store, "client", None) is None and getattr(self._store, "collection", None) is None:
                await self._store.initialize()

    # ---------------------------------------------------------------- write

    async def replace_files(self, user_id: str, device_id: str, files: Sequence[IndexedFile],
                            *, policy: Optional[Mapping[str, Any]] = None) -> Dict[str, int]:
        """Replace each file's chunks. Files outside the device's current boundary are refused (counted)."""
        await self.ready()
        total = sum(len(f.chunks) for f in files)
        if total > MAX_CHUNKS_PER_REQUEST:
            raise IndexError_(f"at most {MAX_CHUNKS_PER_REQUEST} chunks per request")
        if await self.count(user_id) + total > MAX_CHUNKS_PER_USER:
            raise IndexError_("the document index is full; clear some of it first")
        stored = skipped = 0
        for f in files:
            path = _norm(f.path)
            if policy is not None and not allowed_by_policy(path, policy):
                skipped += 1
                continue
            if len(f.chunks) > MAX_CHUNKS_PER_FILE:
                raise IndexError_(f"{f.path}: more than {MAX_CHUNKS_PER_FILE} chunks")
            await self.store.delete_by_metadata({"$and": [{"user_id": user_id}, {"device_id": device_id}, {"path": path}]})
            texts, metas, ids = [], [], []
            for i, c in enumerate(f.chunks):
                text = c.text.replace("\x00", " ").strip()[:MAX_CHUNK_CHARS]
                if not text:
                    continue
                texts.append(text)
                metas.append({"user_id": user_id, "device_id": device_id, "path": path, "ordinal": i,
                              "page": c.page if c.page is not None else -1, "mtime": float(f.mtime),
                              "size": int(f.size)})
                ids.append(chunk_id(user_id, device_id, path, i))
            await self.store.add_documents(texts, metas, ids)
            stored += len(texts)
        return {"chunks_stored": stored, "files_skipped": skipped}

    async def remove(self, user_id: str, device_id: str, *, paths: Sequence[str] = (),
                     prefix: Optional[str] = None) -> int:
        """Remove files' chunks (exact paths, a folder prefix, or everything of that device)."""
        await self.ready()
        base = [{"user_id": user_id}, {"device_id": device_id}]
        removed = 0
        if not paths and not prefix:
            return await self.store.delete_by_metadata({"$and": base})
        for p in paths:
            removed += await self.store.delete_by_metadata({"$and": [*base, {"path": _norm(p)}]})
        if prefix:
            root = _norm(prefix)
            rows = await self.store.get_by_metadata({"$and": base}, limit=100_000)
            ids = [r["id"] for r in rows
                   if r["metadata"].get("path") == root or str(r["metadata"].get("path", "")).startswith(root.rstrip("/") + "/")]
            if ids:
                removed += await self.store.delete_many(ids)
        return removed

    # ---------------------------------------------------------------- read

    async def count(self, user_id: str, device_id: Optional[str] = None) -> int:
        await self.ready()
        where: Dict[str, Any] = {"user_id": user_id} if device_id is None else {"$and": [{"user_id": user_id}, {"device_id": device_id}]}
        return await self.store.count(where=where) if hasattr(self.store, "count") else 0

    async def status(self, user_id: str, device_id: str) -> Dict[str, Any]:
        await self.ready()
        rows = await self.store.get_by_metadata({"$and": [{"user_id": user_id}, {"device_id": device_id}]}, limit=100_000)
        files = {r["metadata"].get("path") for r in rows}
        return {"device_id": device_id, "chunks": len(rows), "files": len(files),
                "last_mtime": max((r["metadata"].get("mtime") or 0 for r in rows), default=0)}

    async def indexed_files(self, user_id: str, device_id: str) -> Dict[str, Dict[str, float]]:
        """path -> {mtime, size}: lets a device index only what changed."""
        await self.ready()
        rows = await self.store.get_by_metadata({"$and": [{"user_id": user_id}, {"device_id": device_id}]}, limit=100_000)
        return {r["metadata"]["path"]: {"mtime": r["metadata"].get("mtime", 0), "size": r["metadata"].get("size", 0)}
                for r in rows if r["metadata"].get("path")}

    async def search(self, user_id: str, query: str, *, limit: int = 5,
                     policies: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
        """Top matches among the user's devices in ``policies`` (device_id -> current path policy).

        A device missing from ``policies`` is not searched (not allowed / remote use off / indexing off)."""
        await self.ready()
        if not query.strip() or not policies:
            return []
        where = {"$and": [{"user_id": user_id}, {"device_id": {"$in": list(policies)}}]}
        raw = await self.store.search(query, limit=min(max(limit, 1), 20) * 4, where=where)
        out: List[Dict[str, Any]] = []
        for r in raw:
            m = r.get("metadata") or {}
            pol = policies.get(m.get("device_id"))
            if pol is None or not allowed_by_policy(str(m.get("path", "")), pol):
                continue                                    # stale chunk of a folder that is no longer allowed
            out.append({"text": r["text"], "path": m.get("path"), "device_id": m.get("device_id"),
                        "page": None if m.get("page", -1) in (-1, None) else m["page"],
                        "score": r.get("distance")})
            if len(out) >= limit:
                break
        return out


def policy_from_effective(effective: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The path policy for search, or None when the user has not turned document indexing on."""
    if not effective.get("indexDocuments"):
        return None
    return path_policy(effective)
