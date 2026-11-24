"""GET /system/tasks/<unknown> is a 404, not a 500."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pai.api.routes.system import admin_get_task
from pai.tasks.store import TaskNotFoundError


async def test_unknown_task_is_404():
    async def missing(task_id): raise TaskNotFoundError(task_id)
    ctx = SimpleNamespace(orchestrator=SimpleNamespace(task_manager=SimpleNamespace(get=missing)))
    with pytest.raises(HTTPException) as e:
        await admin_get_task("nope", context=ctx)
    assert e.value.status_code == 404
