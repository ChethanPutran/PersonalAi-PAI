"""Small local record of what was indexed: path -> (mtime, size, content hash, chunk count). SQLite, mode 0600."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable

FILE_NAME = "document_index.sqlite"


@dataclass(frozen=True)
class Entry:
    mtime: float
    size: int
    digest: str
    chunks: int


class IndexState:
    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / FILE_NAME

    def _connect(self) -> sqlite3.Connection:
        new = not self.path.exists()
        if new:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, "
                     "digest TEXT, chunks INTEGER)")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return conn

    def all(self) -> Dict[str, Entry]:
        if not self.path.exists():
            return {}
        with closing(self._connect()) as c:
            return {r[0]: Entry(r[1], r[2], r[3], r[4]) for r in c.execute("SELECT * FROM files")}

    def count(self) -> int:
        if not self.path.exists():
            return 0
        with closing(self._connect()) as c:
            return c.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def put(self, path: str, entry: Entry) -> None:
        with closing(self._connect()) as c, c:
            c.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)",
                      (path, entry.mtime, entry.size, entry.digest, entry.chunks))

    def delete(self, paths: Iterable[str]) -> None:
        with closing(self._connect()) as c, c:
            c.executemany("DELETE FROM files WHERE path=?", [(p,) for p in paths])

    def clear(self) -> None:
        if self.path.exists():
            with closing(self._connect()) as c, c:
                c.execute("DELETE FROM files")
