"""Walk the files plugin's folders and turn changed files into chunks (blocking code: run it in a thread).

Boundary: the plugin's allowed folders minus its denied folders, using :mod:`pai_device.path_policy`
(``realpath``; a symlink that leads outside is never followed; denied wins). Hidden folders and the usual
dependency/cache folders are skipped. Nothing here talks to the network.
"""
from __future__ import annotations

import hashlib
import os
import stat
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from ..path_policy import PathPolicy, is_within, resolve
from .chunker import chunk_segments
from .extract import ExtractError, SUPPORTED, extract
from .state import Entry

SKIP_DIRS = {"node_modules", "__pycache__", "site-packages", "venv", "env", "build", "dist", "target", "cache", "caches"}
MAX_CHUNKS_PER_FILE = 400        # one request carries a whole file (the server replaces per file) and <= 400 chunks
PER_FILE_SECONDS = 60.0
FILE_MB_DEFAULT = 20


@dataclass
class Item:
    """``send``: chunks to upload. ``unchanged``: nothing to do. ``empty``: no usable text (kept in local state).
    ``skip``: not indexed (``reason``); a skipped file is not 'present' so an old copy on the server is removed."""
    kind: str
    path: str
    mtime: float = 0.0
    size: int = 0
    chunks: List[dict] = field(default_factory=list)
    digest: str = ""
    reason: str = ""
    detail: str = ""
    truncated: bool = False


class Scanner:
    def __init__(self, values: Dict, local: Dict[str, Entry], server: Dict[str, Dict], *,
                 per_file_seconds: float = PER_FILE_SECONDS) -> None:
        self.policy = PathPolicy.from_values(values)
        mb = values.get("indexMaxFileMb")
        self.max_bytes = int((mb if isinstance(mb, (int, float)) and not isinstance(mb, bool) and mb > 0
                              else FILE_MB_DEFAULT) * 1024 * 1024)
        self.local, self.server = local, server
        self.per_file_seconds = per_file_seconds
        self.denied = [resolve(p) for p in self.policy.denied]
        self.roots = self.policy.roots()
        self.stats: Counter = Counter()
        self.missing_roots: List[str] = []

    # -- boundary -----------------------------------------------------------------------------
    def inside(self, real: str) -> bool:
        if any(is_within(real, d) for d in self.denied):
            return False
        return any(is_within(real, r) for r in self.roots)

    def _dir_ok(self, parent: str, name: str) -> bool:
        if name.startswith(".") or name.lower() in SKIP_DIRS:
            return False
        full = os.path.join(parent, name)
        if os.path.islink(full):                         # never follow a directory symlink
            return False
        return self.inside(resolve(full))

    def walk(self) -> Iterator[str]:
        seen = set()
        for root in self.roots:
            if any(is_within(root, d) for d in self.denied):
                continue
            if not os.path.isdir(root):
                self.missing_roots.append(root)
                continue
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if self._dir_ok(dirpath, d))
                for name in sorted(filenames):
                    if name.startswith("."):
                        continue
                    if os.path.splitext(name)[1].lower() not in SUPPORTED:
                        self.stats["unsupported_type"] += 1
                        continue
                    real = resolve(os.path.join(dirpath, name))      # a file symlink is judged by its target
                    if real in seen:
                        continue
                    if not self.inside(real):
                        self.stats["outside_boundary"] += 1
                        continue
                    seen.add(real)
                    yield real

    # -- one file -----------------------------------------------------------------------------
    def _unchanged(self, path: str, mtime: float, size: int) -> bool:
        loc = self.local.get(path)
        if loc is None or abs(loc.mtime - mtime) > 1e-3 or loc.size != size:
            return False
        if loc.chunks == 0:
            return True
        srv = self.server.get(path)
        return bool(srv) and abs(float(srv.get("mtime", -1)) - mtime) <= 1e-3 and int(srv.get("size", -1)) == size

    def items(self) -> Iterator[Item]:
        for path in self.walk():
            try:
                st = os.stat(path)
            except OSError as exc:
                yield Item("skip", path, reason="unreadable", detail=exc.strerror or "")
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if st.st_size > self.max_bytes:
                yield Item("skip", path, reason="too_large")
                continue
            if self._unchanged(path, st.st_mtime, st.st_size):
                yield Item("unchanged", path, st.st_mtime, st.st_size)
                continue
            yield self._read(path, st)

    def _read(self, path: str, st: os.stat_result) -> Item:
        deadline = time.monotonic() + self.per_file_seconds
        chunks: List[dict] = []
        digest = hashlib.sha256()
        truncated = False
        try:
            for text, page in chunk_segments(extract(path, deadline=deadline)):
                if len(chunks) >= MAX_CHUNKS_PER_FILE:
                    truncated = True
                    break
                digest.update(text.encode("utf-8", "replace"))
                chunks.append({"text": text, "page": page} if page is not None else {"text": text})
        except ExtractError as exc:
            return Item("skip", path, reason=exc.reason, detail=str(exc))
        except Exception as exc:                       # a hostile file must never stop the run
            return Item("skip", path, reason="extract_failed", detail=type(exc).__name__)
        try:
            after = os.stat(path)
        except OSError:
            return Item("skip", path, reason="unreadable")
        if after.st_mtime != st.st_mtime or after.st_size != st.st_size:
            return Item("skip", path, reason="changed_while_reading")      # the next run picks it up
        if not chunks:
            return Item("empty", path, st.st_mtime, st.st_size, digest="", reason="empty")
        return Item("send", path, st.st_mtime, st.st_size, chunks, digest.hexdigest(), truncated=truncated)
