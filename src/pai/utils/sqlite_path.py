"""SQLite database location from a config value that may be a SQLAlchemy URL or a plain path.

`memory.long_term.db_url` / `memory.episodic.db_url` are written as URLs (`sqlite+aiosqlite:///./data/x.db`) but
those stores talk to SQLite directly (aiosqlite), so they need a filesystem path. Using `Path(url)` produced the
relative path `sqlite+aiosqlite:/data/x.db` and created a folder with that name in the working directory.
"""
from __future__ import annotations

from pathlib import Path

_SCHEMES = ("sqlite+aiosqlite:///", "sqlite+pysqlite:///", "sqlite:///")


def sqlite_path(value: str | Path) -> Path:
    """`sqlite+aiosqlite:///./data/x.db` -> ./data/x.db, `sqlite+aiosqlite:////abs/x.db` -> /abs/x.db,
    `:memory:` and plain paths unchanged. Any other URL scheme is an error (not a SQLite database)."""
    raw = str(value).strip()
    for scheme in _SCHEMES:
        if raw.lower().startswith(scheme):
            rest = raw[len(scheme):]
            return Path(rest or ":memory:")        # SQLAlchemy: nothing after the slashes = in-memory
    if "://" in raw:
        raise ValueError(f"not a SQLite database URL: {raw!r} (expected sqlite+aiosqlite:///<path> or a file path)")
    return Path(raw)
