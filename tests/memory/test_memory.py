import pytest
from pai.memory.short_term import ShortTermMemory
from pai.memory.long_term import LongTermMemory
from pai.memory.episodic import EpisodicMemory
from pai.memory.vector_store import VectorStore

@pytest.mark.asyncio
async def test_short_term_memory():
    stm = ShortTermMemory(max_size=2)
    await stm.initialize()
    await stm.add({"msg": "hello"})
    await stm.add({"msg": "world"})
    recent = await stm.get_recent(10)
    assert len(recent) == 2
    await stm.clear()
    assert len(await stm.get_recent(10)) == 0

@pytest.mark.asyncio
async def test_long_term_memory(tmp_path):
    db_path = tmp_path / "test.db"
    ltm = LongTermMemory(str(db_path))
    await ltm.initialize()
    await ltm.set_preference("theme", "dark")
    value = await ltm.get_preference("theme")
    assert value == "dark"
    await ltm.shutdown()

@pytest.mark.asyncio
async def test_episodic_memory(tmp_path):
    db_path = tmp_path / "episodic.db"
    ep = EpisodicMemory(str(db_path))
    await ep.initialize()
    await ep.add({"event": "user_logged_in"})
    similar = await ep.get_similar("login", limit=1)
    assert len(similar) == 1
    assert similar[0]["event"] == "user_logged_in"

@pytest.mark.asyncio
async def test_vector_store(tmp_path):
    vs = VectorStore(persist_dir=str(tmp_path))
    await vs.initialize()
    await vs.add_document("This is a test document", {"source": "test"})
    results = await vs.search("test document")
    assert len(results) >= 1