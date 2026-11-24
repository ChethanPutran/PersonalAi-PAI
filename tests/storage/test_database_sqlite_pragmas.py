"""SQLite tuning: WAL + busy_timeout, so concurrent writers don't hit 'database is locked'."""
import asyncio

from sqlalchemy import text

from pai.config.models.database import DatabaseConfig
from pai.storage.database import Database


async def test_sqlite_is_tuned_and_survives_concurrent_writers(tmp_path):
    db = Database(DatabaseConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'c.db'}"))
    async with db.engine.begin() as conn:
        await conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        assert (await conn.execute(text("PRAGMA journal_mode"))).scalar() == "wal"
        assert (await conn.execute(text("PRAGMA busy_timeout"))).scalar() == 5000

    async def writer(i: int) -> None:
        for j in range(10):
            async with db.session() as s:
                await s.execute(text("INSERT INTO t (v) VALUES (:v)"), {"v": f"{i}-{j}"})

    await asyncio.gather(*(writer(i) for i in range(30)))          # 300 contended writes
    async with db.session() as s:
        assert (await s.execute(text("SELECT COUNT(*) FROM t"))).scalar() == 300
    await db.engine.dispose()
