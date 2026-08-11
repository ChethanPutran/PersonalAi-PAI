import pytest
from pai.executors.server_executor import ServerExecutor
from pai.executors.desktop_executor import DesktopExecutor

@pytest.mark.asyncio
async def test_server_executor():
    executor = ServerExecutor()
    await executor.initialize()
    result = await executor.execute_task({"type": "llm_reason", "goal": "test"})
    assert "response" in result

@pytest.mark.asyncio
async def test_desktop_executor():
    executor = DesktopExecutor()
    await executor.initialize()
    # Test file operation (temp file)
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as f:
        f.write("content")
        path = f.name
    result = await executor.execute_task({"type": "file_operation", "operation": "read", "path": path})
    assert "content" in result
    import os
    os.unlink(path)