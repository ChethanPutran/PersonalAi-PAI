"""Lightweight, idempotent schema upgrades for existing SQLite databases.

``Base.metadata.create_all`` creates missing TABLES but never adds a COLUMN to a table that
already exists, so a database created by an older release would crash on the new ORM
columns. Each migration below is "add this column if it is not there yet": safe to run on
every start, and a no-op on a fresh database.
"""
from __future__ import annotations

from typing import List, NamedTuple

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


class AddColumn(NamedTuple):
    table: str
    column: str
    ddl: str          # SQLite column definition; NOT NULL columns must carry a DEFAULT


MIGRATIONS: List[AddColumn] = [
    # scheduled plugin calls
    AddColumn("schedules", "requires_consent", "BOOLEAN NOT NULL DEFAULT 0"),
    AddColumn("schedules", "risk", "VARCHAR(16) NOT NULL DEFAULT 'low'"),
    AddColumn("schedules", "executor", "VARCHAR(32)"),
    AddColumn("schedules", "consent_granted_at", "DATETIME"),
    AddColumn("schedules", "consent_expires_at", "DATETIME"),
    AddColumn("schedules", "consent_granted_by", "VARCHAR"),
    AddColumn("schedules", "consent_binding_hash", "VARCHAR(64)"),
    AddColumn("schedules", "last_run_at", "DATETIME"),
    AddColumn("schedules", "last_run_status", "VARCHAR(24)"),
    AddColumn("schedules", "last_run_error", "TEXT"),
    AddColumn("schedules", "last_task_id", "VARCHAR"),
    AddColumn("schedules", "misfire_grace_seconds", "INTEGER"),
    # what the user allowed on the device for each installed plugin (device-reported, advisory)
    AddColumn("device_plugins", "permissions", "JSON"),
    AddColumn("device_plugins", "permissions_reported_at", "DATETIME"),
    # user profile
    AddColumn("users", "display_name", "VARCHAR(80)"),
    AddColumn("users", "timezone", "VARCHAR(64)"),
    AddColumn("users", "locale", "VARCHAR(16)"),
    # per-device switches for remote plugin install / invocation (off by default)
    AddColumn("devices", "allow_remote_install", "BOOLEAN NOT NULL DEFAULT 0"),
    AddColumn("devices", "allow_remote_invoke", "BOOLEAN NOT NULL DEFAULT 0"),
]


async def upgrade_sqlite_schema(conn: AsyncConnection) -> List[str]:
    """Add every missing column. Returns "table.column" for each one added."""
    added: List[str] = []
    for m in MIGRATIONS:
        # Table names/DDL come from the constant list above, never from input.
        rows = (await conn.execute(text(f"PRAGMA table_info({m.table})"))).fetchall()
        if not rows:                       # table does not exist (create_all makes it with all columns)
            continue
        if m.column in {r[1] for r in rows}:
            continue
        await conn.execute(text(f"ALTER TABLE {m.table} ADD COLUMN {m.column} {m.ddl}"))
        added.append(f"{m.table}.{m.column}")
    if added:
        logger.info("Database upgraded: added columns {}", ", ".join(added))
    return added
