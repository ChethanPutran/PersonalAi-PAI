import pytest
import asyncio
from typing import AsyncGenerator
from pai.kernel.ai_kernel import AIKernel
from pai.memory.memory_manager import MemoryManager
from pai.event_bus.event_bus import EventBus
from pai.agents.agent_manager import AgentManager
from pai.plugins.plugin_manager import PluginManager

@pytest.fixture
async def kernel() -> AsyncGenerator[AIKernel, None]:
    """Create and initialize a kernel instance for testing."""
    k = AIKernel()
    await k.initialize()
    yield k
    await k.stop()

@pytest.fixture
async def memory_manager() -> AsyncGenerator[MemoryManager, None]:
    mm = MemoryManager()
    await mm.initialize()
    yield mm
    await mm.shutdown()

@pytest.fixture
async def event_bus() -> AsyncGenerator[EventBus, None]:
    eb = EventBus()
    await eb.initialize()
    await eb.start()
    yield eb
    await eb.stop()

@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()