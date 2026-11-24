"""Rows written by older schemas must load (and one bad row must not break the list)."""
from datetime import datetime, timezone

import pytest

from pai.storage.models import TaskModel
from pai.storage.repositories.task import TaskRepository, upgrade_legacy_payload
from pai.tasks.models import Task, TaskStatus, TaskType

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc).isoformat()


def legacy(i="t-old-1", type_="open_image_viewer"):
    """Shape of a task written before name/TaskType existed (copied from a real database)."""
    return {
        "id": i, "title": f"Task for {type_}", "user_id": "u1", "session_id": "s1", "source": "user",
        "input": "image.open", "type": type_, "action": None, "parameters": {"path": "/tmp/x.jpg"},
        "plugin": None, "capability": "image.open", "device_id": "self", "executor_id": "builtin_executor",
        "status": "completed", "priority": "normal", "timeout": 30.0, "max_retries": 0, "retry_count": 0,
        "created_at": NOW, "initialized_at": NOW, "queued_at": NOW, "started_at": NOW, "completed_at": NOW,
        "result": {"success": True, "output": {"ok": 1}, "error": None, "executor_id": "builtin_executor",
                   "device_id": "self", "started_at": NOW, "completed_at": NOW},
        "error": None, "metadata": {"plugin": "builtin"}, "parent_task_id": None, "child_task_ids": [],
    }


def test_upgrade_fixes_all_three_legacy_problems():
    t = Task(**upgrade_legacy_payload(legacy()))
    assert t.name == "Task for open_image_viewer"                      # title -> name
    assert t.type == TaskType.ONE_SHOT                                 # free-form type -> TaskType
    assert t.metadata["legacy_type"] == "open_image_viewer" and t.metadata["plugin"] == "builtin"
    assert t.capability == "image.open"                                # existing capability untouched
    assert t.result.task_id == "t-old-1"                               # result gained its task_id


def test_legacy_type_becomes_the_capability_when_there_was_none():
    p = legacy(); p["capability"] = None
    assert Task(**upgrade_legacy_payload(p)).capability == "open_image_viewer"


def test_current_format_payloads_pass_through_unchanged():
    current = Task(name="n", type=TaskType.LONG_RUNNING, user_id="u1", capability="a.b").model_dump(mode="json")
    assert upgrade_legacy_payload(current) == current


@pytest.fixture
async def repo(session_factory):
    return TaskRepository(session_factory)


async def _insert(session_factory, payload, *, user="u1", status="completed"):
    async with session_factory() as s:
        s.add(TaskModel(id=payload["id"], status=status, user_id=user, session_id="s1",
                        parent_task_id=None, created_at=datetime.now(timezone.utc), payload=payload))
        await s.commit()


async def test_list_returns_upgraded_legacy_rows_and_skips_only_the_corrupt_one(repo, session_factory):
    await _insert(session_factory, legacy("a", "create_reminder"))
    await _insert(session_factory, legacy("b", "capture_photo"))
    await _insert(session_factory, {"id": "broken", "status": "not-a-real-status", "created_at": "not-a-date"}, status="failed")        # unrecoverable
    current = Task(name="new", type=TaskType.ONE_SHOT, user_id="u1", session_id="s1")
    await repo.create(current)

    tasks = await repo.list(user_id="u1")
    assert {t.id for t in tasks} == {"a", "b", current.id}                # list works; corrupt row skipped
    assert all(isinstance(t.status, TaskStatus) for t in tasks)
    assert (await repo.get("a")).name == "Task for create_reminder"       # get() upgrades too


def test_serialized_task_carries_everything_the_app_displays():
    from pai.api.routes.system import _serialize_task
    legacy_task = Task(**upgrade_legacy_payload(legacy()))
    out = _serialize_task(legacy_task)
    assert out["name"] == "Task for open_image_viewer" and out["title"] == "Task for open_image_viewer"
    assert out["type"] == "one_shot" and out["capability"] == "image.open"
    assert out["priority"] == "normal" and out["max_retries"] == 0 and out["timeout"] == 30.0
    assert out["initialized_at"] and out["queued_at"]
    modern = Task(name="Turn on lights", type=TaskType.LONG_RUNNING, capability="lights.on", user_id="u1")
    m = _serialize_task(modern)
    assert m["title"] == "Turn on lights" and m["type"] == "long_running"
    assert "authorization" not in m and "tokens" not in str(m).lower()      # never leak tokens
