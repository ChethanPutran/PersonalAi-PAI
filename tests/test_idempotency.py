"""Goal-level idempotency: registry semantics, the real /chat route, direct-action dedupe."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from pai.api.dependencies import get_current_user
from pai.api.routes import chat as chat_routes
from pai.app_context import get_db_session, get_orchestrator
from pai.orchestration.execution_context import ContextManager
from pai.orchestration.idempotency import (
    IdempotencyKey, IdempotencyPolicy, IdempotencyRegistry, IdempotentRunAborted, Role,
)
from pai.orchestration.orchestrator import TaskOrchestrator, action_idempotency_key
from pai.storage.models import ChatMessageModel


def K(key="k1", user="u1", session="s1"):
    return IdempotencyKey(user_id=user, session_id=session, key=key)


# ───────────────────────── registry ─────────────────────────

async def test_owner_then_replay_and_joiner_share_one_run():
    reg = IdempotencyRegistry(IdempotencyPolicy(success_ttl_seconds=60))
    runs = []

    async def call():
        async with reg.guard(K()) as g:
            if g.is_replay:
                return g.role, g.replay
            runs.append(1)
            await asyncio.sleep(.1)
            g.set_result("RESULT")
            return g.role, "RESULT"

    a, b = await asyncio.gather(call(), call())           # concurrent duplicate
    c = await call()                                      # later duplicate
    assert len(runs) == 1
    assert {a[0], b[0]} == {Role.OWNER, Role.JOINER}
    assert a[1] == b[1] == c[1] == "RESULT" and c[0] == Role.REPLAY


async def test_scopes_never_collide():
    reg = IdempotencyRegistry()
    seen = []
    for key in (K(user="u1"), K(user="u2"), K(session="s2"), K(key="k2")):
        async with reg.guard(key) as g:
            seen.append(g.role)
            g.set_result("x")
    assert seen == [Role.OWNER] * 4


async def test_failed_outcome_cached_only_briefly():
    reg = IdempotencyRegistry(IdempotencyPolicy(success_ttl_seconds=60, failure_ttl_seconds=0.1))
    async with reg.guard(K()) as g:
        g.set_result("boom", success=False)
    async with reg.guard(K()) as g:
        assert g.role == Role.REPLAY                      # within the short TTL
    await asyncio.sleep(.15)
    async with reg.guard(K()) as g:
        assert g.role == Role.OWNER                       # expired: client may retry for real


async def test_success_expires_after_ttl():
    reg = IdempotencyRegistry(IdempotencyPolicy(success_ttl_seconds=0.1))
    async with reg.guard(K()) as g:
        g.set_result("ok")
    await asyncio.sleep(.15)
    async with reg.guard(K()) as g:
        assert g.role == Role.OWNER


async def test_owner_exception_caches_nothing_and_joiners_are_told():
    reg = IdempotencyRegistry()
    started = asyncio.Event()

    async def owner():
        async with reg.guard(K()):
            started.set()
            await asyncio.sleep(.1)
            raise RuntimeError("boom")

    async def joiner():
        await started.wait()
        async with reg.guard(K()):
            pass

    o, j = await asyncio.gather(owner(), joiner(), return_exceptions=True)
    assert isinstance(o, RuntimeError) and isinstance(j, IdempotentRunAborted)
    async with reg.guard(K()) as g:                       # nothing cached
        assert g.role == Role.OWNER
        g.set_result("ok")


async def test_cancelled_owner_aborts_cleanly():
    reg = IdempotencyRegistry()
    started = asyncio.Event()

    async def owner():
        async with reg.guard(K()):
            started.set()
            await asyncio.sleep(10)

    t = asyncio.create_task(owner()); await started.wait()
    t.cancel(); await asyncio.gather(t, return_exceptions=True)
    async with reg.guard(K()) as g:
        assert g.role == Role.OWNER; g.set_result("ok")


async def test_no_key_is_a_noop_guard():
    reg = IdempotencyRegistry()
    for _ in range(2):
        async with reg.guard(None) as g:
            assert g.role == Role.OWNER
            g.set_result("x")


def test_key_validation_and_header_parsing():
    assert IdempotencyKey.from_header(None, user_id="u", session_id=None) is None
    assert IdempotencyKey.from_header("  ", user_id="u", session_id=None) is None
    assert IdempotencyKey.from_header("abc-1", user_id="u", session_id=None).session_id == ""
    for bad in ("has space", "x" * 129, "semi;colon", "new\nline"):
        with pytest.raises(ValueError):
            IdempotencyKey.from_header(bad, user_id="u", session_id=None)


def test_action_key_is_stable_across_param_order_and_distinct_otherwise():
    a = action_idempotency_key("r1", "notification.send", {"a": 1, "b": 2})
    assert a == action_idempotency_key("r1", "notification.send", {"b": 2, "a": 1})
    assert a != action_idempotency_key("r1", "notification.send", {"a": 1, "b": 3})
    assert a != action_idempotency_key("r2", "notification.send", {"a": 1, "b": 2})
    assert a.startswith("r1:notification.send:") and len(a.rsplit(":", 1)[1]) == 16


# ───────────────────────── real /chat route over HTTP ─────────────────────────

class FakeOrchestrator:
    def __init__(self, delay=0.15):
        self.idempotency = IdempotencyRegistry(IdempotencyPolicy(failure_ttl_seconds=0.1))
        self.delay, self.calls, self.fail = delay, [], False

    async def run(self, *, user_id, goal, session_id, source_device_id, context, request_id=None):
        self.calls.append(request_id)
        await asyncio.sleep(self.delay)
        return {"status": "failed" if self.fail else "completed", "request_id": request_id or "gen",
                "plan_id": "p", "results": [{"task_id": "t", "status": "completed", "reply": "hello"}],
                "failed": ["x"] if self.fail else []}


@pytest.fixture
async def chat_client(session_factory):
    orch = FakeOrchestrator()
    app = FastAPI()
    app.include_router(chat_routes.router)   # router already carries prefix="/chat"
    user = {"id": "u1"}

    async def db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_current_user] = lambda: user["id"]
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.dependency_overrides[get_db_session] = db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield SimpleNamespace(c=c, orch=orch, user=user, sf=session_factory)


BODY = {"device_id": "d", "message": "take a photo", "session_id": "s1"}


async def _user_messages(sf):
    async with sf() as s:
        rows = (await s.execute(select(ChatMessageModel))).scalars().all()
    return [r for r in rows if r.role == "user"]


async def test_duplicate_post_runs_goal_once_and_persists_one_user_turn(chat_client):
    h = {"Idempotency-Key": "req-abc"}
    r1, r2 = await asyncio.gather(
        chat_client.c.post("/chat", json=BODY, headers=h), chat_client.c.post("/chat", json=BODY, headers=h))
    r3 = await chat_client.c.post("/chat", json=BODY, headers=h)

    assert [r.status_code for r in (r1, r2, r3)] == [200, 200, 200]
    assert chat_client.orch.calls == ["req-abc"]                       # ran once; key IS the request_id
    assert r1.json() == r2.json() == r3.json()
    assert sorted(r.headers.get("idempotent-replay", "false") for r in (r1, r2)) == ["false", "true"]
    assert r3.headers["idempotent-replay"] == "true"
    assert len(await _user_messages(chat_client.sf)) == 1              # not persisted twice


async def test_without_key_every_post_runs(chat_client):
    await chat_client.c.post("/chat", json=BODY)
    await chat_client.c.post("/chat", json=BODY)
    assert chat_client.orch.calls == [None, None]


async def test_other_user_or_session_with_same_key_runs_separately(chat_client):
    h = {"Idempotency-Key": "same"}
    await chat_client.c.post("/chat", json=BODY, headers=h)
    await chat_client.c.post("/chat", json={**BODY, "session_id": "s2"}, headers=h)
    chat_client.user["id"] = "u2"
    # (u2 can't use u1's session id)
    await chat_client.c.post("/chat", json={**BODY, "session_id": "s3"}, headers=h)
    assert chat_client.orch.calls == ["same", "same", "same"]


async def test_failed_run_is_retryable_after_short_ttl(chat_client):
    h = {"Idempotency-Key": "f1"}
    chat_client.orch.fail = True
    r1 = await chat_client.c.post("/chat", json=BODY, headers=h)
    assert r1.json()["status"] == "failed"
    r2 = await chat_client.c.post("/chat", json=BODY, headers=h)       # inside failure TTL: replayed
    assert r2.headers["idempotent-replay"] == "true" and len(chat_client.orch.calls) == 1
    await asyncio.sleep(.15)
    chat_client.orch.fail = False
    r3 = await chat_client.c.post("/chat", json=BODY, headers=h)       # now a real retry
    assert r3.json()["status"] == "completed" and len(chat_client.orch.calls) == 2


async def test_bad_key_is_422(chat_client):
    r = await chat_client.c.post("/chat", json=BODY, headers={"Idempotency-Key": "bad key!"})
    assert r.status_code == 422 and chat_client.orch.calls == []


# ───────────────────────── direct action path ─────────────────────────

async def test_execute_action_twice_with_same_request_runs_once(task_manager, capability_router, security_manager):
    from pai.config.models.executor import ExecutorConfig  # noqa: F401
    orch = TaskOrchestrator.__new__(TaskOrchestrator)
    orch.context_manager = ContextManager()
    orch.task_manager = task_manager
    orch.capability_resolver = task_manager.capability_resolver
    orch.device_selector = task_manager.device_selector
    orch.security_manager = security_manager
    orch.idempotency = IdempotencyRegistry()

    r1 = await orch.execute_action(user_id="u", capability="test.echo", params={"a": 1},
                                   session_id="s", request_id="req-act")
    r2 = await orch.execute_action(user_id="u", capability="test.echo", params={"a": 1},
                                   session_id="s", request_id="req-act")
    r3 = await orch.execute_action(user_id="u", capability="test.echo", params={"a": 2},
                                   session_id="s", request_id="req-act")        # different params
    tasks = await task_manager.list()
    assert len(tasks) == 2                                            # a=1 once, a=2 once
    assert r2["task_id"] == r1["task_id"] and r2["idempotent_replay"] is True
    assert r3["task_id"] != r1["task_id"]
    assert next(t for t in tasks if t.id == r1["task_id"]).idempotency_key == \
        action_idempotency_key("req-act", "test.echo", {"a": 1})
