#!/usr/bin/env python3
"""
Database setup script for the Personal AI System.
Initializes SQLite databases for long-term memory, episodic memory, and relational data.
"""

from src.pai.models.task import Base as TaskBase
from src.pai.models.user import Base
from src.pai.config import config
import sys
import asyncio
from pathlib import Path
import aiosqlite
from loguru import logger

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Database paths
DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)

LONG_TERM_DB = DATA_DIR / "long_term.db"
EPISODIC_DB = DATA_DIR / "episodic.db"
RELATIONAL_DB = DATA_DIR / "pai.db"  # SQLite fallback for SQLAlchemy


async def init_long_term_memory():
    """Initialize long_term.db with memories table."""
    async with aiosqlite.connect(LONG_TERM_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(key)
        """)
        await db.commit()
    logger.info(f"Long-term memory database initialized at {LONG_TERM_DB}")


async def init_episodic_memory():
    """Initialize episodic.db with episodes table."""
    async with aiosqlite.connect(EPISODIC_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_timestamp ON episodes(timestamp)
        """)
        await db.execute("""
        ALTER TABLE episodes ADD COLUMN embedding BLOB;
CREATE INDEX idx_episodes_embedding ON episodes(embedding);  -- not standard, but keep
        """)
        await db.commit()
    logger.info(f"Episodic memory database initialized at {EPISODIC_DB}")


async def init_relational_db():
    """Initialize relational database for users, tasks, etc."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.pai.models.user import Base as UserBase
    from src.pai.models.task import Base as TaskBase
    from src.pai.config import config

    # Use SQLite by default, but can be overridden by config
    db_url = config.database.url
    if db_url.startswith("sqlite"):
        # Ensure directory exists
        db_path = db_url.replace("sqlite+aiosqlite:///", "")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(db_url, echo=config.debug)
    UserBase.metadata.create_all(engine)
    TaskBase.metadata.create_all(engine)
    logger.info(f"Relational database initialized at {db_url}")


async def init_vector_store():
    """Initialize ChromaDB vector store directory."""
    chroma_dir = Path(config.memory.vector_db_path)
    chroma_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Vector store directory created at {chroma_dir}")


async def main():
    """Run all database initializations."""
    logger.info("Setting up databases for Personal AI System...")
    await init_long_term_memory()
    await init_episodic_memory()
    await init_relational_db()
    await init_vector_store()
    logger.info("All databases initialized successfully.")


if __name__ == "__main__":
    asyncio.run(main())
