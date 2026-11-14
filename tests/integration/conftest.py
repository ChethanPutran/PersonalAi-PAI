# tests/integration/conftest.py
import pytest_asyncio
from pai.config import Config
from pai.app_context import create_app_context


@pytest_asyncio.fixture
async def kernel():
    config = Config.from_env()               # reads .env
    ctx = create_app_context(config)
    await ctx.database.initialize()
    await ctx.orchestrator.initialize()
    yield ctx.orchestrator
    await ctx.orchestrator.shutdown()
    await ctx.database.close()