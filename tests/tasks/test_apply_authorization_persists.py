"""The confirmation gate must survive the DB round-trip.

Regression: ``apply_authorization`` set ``requires_confirmation`` on the in-memory task only. With the
DB-backed store ``_execute`` loads a fresh copy, so the flag was gone and the camera ran with no prompt.
"""
from types import SimpleNamespace

import pytest

from pai.security.authorization.decision import AuthorizationDecision, DecisionEffect
from pai.storage.repositories.task import TaskRepository
from pai.tasks.manager import TaskManager
from pai.tasks.models import Task, TaskType


def _manager(repo):
    m = TaskManager.__new__(TaskManager)          # only apply_authorization is under test
    m.store = repo
    m.authorizer = SimpleNamespace()
    return m


async def _task(repo):
    t = Task(name="shot", type=TaskType.ONE_SHOT, capability="camera.capture", user_id="u1", device_id="d1")
    await repo.create(t)
    return t


CTX = SimpleNamespace(agent_id=None, origin=None, source_device_id="d1", pre_approved=False,
                      allowed_devices=["d1"], filesystem_roots=[], network_hosts=[], user_roles=[])


@pytest.mark.asyncio
async def test_approval_required_is_visible_to_the_next_loader(session_factory, monkeypatch):
    repo = TaskRepository(session_factory)
    t = await _task(repo)
    monkeypatch.setattr("pai.tasks.manager.ScopeContext.from_execution_context",
                        lambda ctx: __import__("pai.security.authorization.context", fromlist=["x"]).ScopeContext())
    decision = AuthorizationDecision(DecisionEffect.APPROVAL_REQUIRED, "needs approval", "r1",
                                     confirmation_reason="Take a photo")
    await _manager(repo).apply_authorization(t, decision, CTX)

    fresh = await repo.require(t.id)                  # what _execute sees
    assert fresh.requires_confirmation is True
    assert fresh.authorization.pending is True
    assert fresh.confirmation_reason == "Take a photo"
