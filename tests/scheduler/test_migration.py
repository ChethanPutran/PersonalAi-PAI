"""A database created by the previous release gets the new columns, safely and repeatably."""
import sqlite3
from datetime import datetime, timezone

from sqlalchemy import text

from pai.config.models.database import DatabaseConfig
from pai.storage.database import Database
from pai.storage.models import ScheduleModel
from pai.storage.repositories.schedule import ScheduleRepository
from pai.tasks.models import ScheduleOut

NEW = {"requires_consent", "risk", "executor", "consent_granted_at", "consent_expires_at",
       "consent_granted_by", "consent_binding_hash", "last_run_at", "last_run_status",
       "last_run_error", "last_task_id"}

OLD_SCHEMA = """
CREATE TABLE schedules (
    id VARCHAR NOT NULL PRIMARY KEY, user_id VARCHAR NOT NULL, target_device_id VARCHAR,
    title VARCHAR(255) NOT NULL, body TEXT, kind VARCHAR(16) NOT NULL, fire_at DATETIME,
    cron VARCHAR(100), interval_seconds INTEGER, timezone VARCHAR(64) NOT NULL,
    capability VARCHAR(128) NOT NULL, parameters JSON NOT NULL, catch_up VARCHAR(8) NOT NULL,
    enabled BOOLEAN NOT NULL, last_fired_at DATETIME, next_fire_at DATETIME,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
"""


def columns(path) -> set:
    with sqlite3.connect(path) as c:
        return {r[1] for r in c.execute("PRAGMA table_info(schedules)")}


async def test_old_database_is_upgraded_old_rows_survive_and_it_is_repeatable(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:                         # a database exactly as the old release left it
        c.executescript(OLD_SCHEMA)
        c.execute("INSERT INTO schedules VALUES ('old-1','u1',NULL,'Call Mom','at noon','one_shot',"
                  "'2026-12-01 12:00:00',NULL,NULL,'UTC','system.notifications.show','{\"title\":\"Call Mom\"}',"
                  "'once',1,NULL,'2026-12-01 12:00:00','2026-11-01 00:00:00','2026-11-01 00:00:00')")
    assert not (NEW & columns(path))

    db = Database(DatabaseConfig(url=f"sqlite+aiosqlite:///{path}"))
    await db.initialize()
    assert NEW <= columns(path)                              # every new column was added

    async with db.session() as s:                            # the old row still loads, with safe defaults
        row = await ScheduleRepository(s).get("old-1")
        assert (row.requires_consent, row.risk, row.last_run_status, row.consent_binding_hash) == (False, "low", None, None)
        out = ScheduleOut.from_row(row)
        assert out.requires_consent is False and out.consent.active is False and out.last_run_status is None
        assert out.capability == "system.notifications.show" and out.title == "Call Mom"

    async with db.session() as s:                            # new rows work against the upgraded table
        s.add(ScheduleModel(id="new-1", user_id="u1", title="t", kind="interval", interval_seconds=60,
                            capability="camera.snap", parameters={}, requires_consent=True, risk="high",
                            last_run_status="skipped_no_consent",
                            last_run_at=datetime.now(timezone.utc)))
    async with db.session() as s:
        got = await ScheduleRepository(s).get("new-1")
        assert got.requires_consent is True and got.last_run_status == "skipped_no_consent"
    await db.engine.dispose()

    before = columns(path)                                   # running again changes nothing (idempotent)
    db2 = Database(DatabaseConfig(url=f"sqlite+aiosqlite:///{path}"))
    await db2.initialize()
    await db2.initialize()
    assert columns(path) == before
    async with db2.session() as s:
        assert (await s.execute(text("SELECT COUNT(*) FROM schedules"))).scalar() == 2
    await db2.engine.dispose()


async def test_a_fresh_database_gets_every_column_from_create_all(tmp_path):
    path = tmp_path / "fresh.db"
    db = Database(DatabaseConfig(url=f"sqlite+aiosqlite:///{path}"))
    await db.initialize()
    assert NEW <= columns(path)
    await db.engine.dispose()
