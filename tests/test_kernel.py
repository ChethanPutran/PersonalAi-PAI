import pytest
from pai.kernel.ai_kernel import AIKernel

@pytest.mark.asyncio
async def test_kernel_initialization(kernel):
    assert kernel._initialized is True
    assert kernel._running is False  # not started yet

@pytest.mark.asyncio
async def test_kernel_start_stop(kernel):
    await kernel.start()
    assert kernel._running is True
    await kernel.stop()
    assert kernel._running is False

@pytest.mark.asyncio
async def test_kernel_process_goal(kernel):
    await kernel.start()
    result = await kernel.process_goal("Test goal", {"user": "test"})
    assert "plan_id" in result
    assert result["status"] == "completed"
    await kernel.stop()

@pytest.mark.asyncio
async def test_kernel_handle_event(kernel):
    await kernel.start()
    await kernel.handle_event("test.event", {"data": 123})
    # No exception means success
    await kernel.stop()