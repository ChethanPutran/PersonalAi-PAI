import pytest
import asyncio
from pai.kernel.ai_kernel import AIKernel

@pytest.mark.asyncio
async def test_concurrent_goals():
    kernel = AIKernel()
    await kernel.start()
    tasks = [kernel.process_goal(f"Goal {i}", {}) for i in range(10)]
    results = await asyncio.gather(*tasks)
    assert len(results) == 10
    await kernel.stop()