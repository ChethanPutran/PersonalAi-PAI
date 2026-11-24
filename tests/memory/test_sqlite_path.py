"""The memory stores must open the SQLite file named by their config URL, not create a folder called
"sqlite+aiosqlite:" in the working directory (regression: Path("sqlite+aiosqlite:///./data/x.db") is the relative
path "sqlite+aiosqlite:/data/x.db")."""
from pathlib import Path

import pytest

from pai.config.models.memory import EpisodicMemoryConfig, LongTermMemoryConfig
from pai.memory.episodic import EpisodicMemory
from pai.memory.long_term import LongTermMemory
from pai.utils.sqlite_path import sqlite_path


@pytest.mark.parametrize("value,expected", [
    ("sqlite+aiosqlite:///./data/x.db", "data/x.db"),
    ("sqlite+aiosqlite:///data/x.db", "data/x.db"),
    ("sqlite+aiosqlite:////abs/x.db", "/abs/x.db"),
    ("sqlite:///./x.db", "x.db"),
    ("./data/a.db", "data/a.db"),
    ("/abs/a.db", "/abs/a.db"),
    (":memory:", ":memory:"),
    ("sqlite+aiosqlite:///", ":memory:"),
    (Path("rel/a.db"), "rel/a.db"),
])
def test_urls_and_plain_paths_resolve_to_a_file_path(value, expected):
    assert str(sqlite_path(value)) == expected


def test_a_non_sqlite_url_is_rejected_clearly():
    with pytest.raises(ValueError, match="not a SQLite database URL"):
        sqlite_path("postgresql+asyncpg://u:p@host/db")


async def test_stores_create_their_database_where_the_url_says(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lt = LongTermMemory(LongTermMemoryConfig(db_url="sqlite+aiosqlite:///./data/long_term.db"))
    ep = EpisodicMemory(EpisodicMemoryConfig(db_url="sqlite+aiosqlite:///./data/episodic.db"))
    await lt.initialize()
    await ep.initialize()
    await lt.shutdown()
    await ep.shutdown()
    assert (tmp_path / "data" / "long_term.db").is_file() and (tmp_path / "data" / "episodic.db").is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data"]            # no "sqlite+aiosqlite:" folder


def test_the_shipped_config_resolves_to_the_data_folder():
    import yaml
    cfg = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "config.yaml").read_text())["memory"]
    assert str(sqlite_path(cfg["long_term"]["db_url"])) == "data/long_term.db"
    assert str(sqlite_path(cfg["episodic"]["db_url"])) == "data/episodic.db"
