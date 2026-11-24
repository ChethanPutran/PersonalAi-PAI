"""SSE twin of the task-status WebSocket: stream framing, access rules, auth scoping."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request

from pai.api.middleware.auth import AuthMiddleware
from pai.api.routes import tasks as tasks_routes
from pai.api.routes.task_stream import SessionEventStream, sse_events
from pai.common.constants import API_PREFIX
from pai.config.models.event_bus import EventBusConfig
from pai.event_bus.bus import EventBus
from pai.security.tokens.auth import create_access_token


@pytest_asyncio.fixture
async def bus():
    b = EventBus(config=EventBusConfig())
    await b.initialize()
    await b.start()
    yield b
    await b.shutdown()


def parse(frame: str) -> dict:
    """'id: 1\\nevent: x\\ndata: {...}\\n\\n' -> {'id': '1', 'event': 'x', 'data': {...}}"""
    out = {}
    for line in frame.strip().splitlines():
        k, _, v = line.partition(": ")
        out[k] = json.loads(v) if k == "data" else v
    return out


async def gone_after(n: int):
    """is_disconnected(): False for n calls, then True."""
    calls = {"n": 0}

    async def is_disconnected():
        calls["n"] += 1
        return calls["n"] > n
    return is_disconnected


async def test_stream_frames_ids_filtering_and_cleanup(bus):
    stream = SessionEventStream(bus, "s1")
    async with stream:
        await bus.publish("task.started", {"session_id": "s1", "task_id": "t1"})
        await bus.publish("task.started", {"session_id": "OTHER", "task_id": "x"})   # other session: filtered
        await bus.publish("dag.task_finished", {"session_id": "s1", "task_id": "a", "status": "completed"})
        await bus.publish("chat.status", {"session_id": "s1"})                       # not task./dag.: ignored
        await asyncio.sleep(.1)

        chunks = []
        is_disc = await gone_after(2)
        async for c in sse_events(stream, session_id="s1", is_disconnected=is_disc, heartbeat_seconds=.05):
            chunks.append(c)

    assert chunks[0].startswith("retry: ") and chunks[1].startswith(": connected session=s1")
    frames = [parse(c) for c in chunks if c.startswith("id:")]
    assert [(f["id"], f["event"]) for f in frames] == [("1", "task.started"), ("2", "dag.task_finished")]
    assert frames[0]["data"]["task_id"] == "t1" and frames[1]["data"]["status"] == "completed"
    assert all("OTHER" not in json.dumps(f) for f in frames)
    # unsubscribed on exit
    assert not any(bus._subscriptions.get(p) for p in ("task.*", "dag.*"))


async def test_heartbeat_comments_when_idle(bus):
    stream = SessionEventStream(bus, "s1")
    async with stream:
        is_disc = await gone_after(3)
        chunks = [c async for c in sse_events(stream, session_id="s1", is_disconnected=is_disc, heartbeat_seconds=.03)]
    assert chunks.count(": keep-alive\n\n") >= 2
    assert not any(c.startswith("id:") for c in chunks)


async def test_stream_ends_when_client_disconnects(bus):
    stream = SessionEventStream(bus, "s1")
    async with stream:
        it = sse_events(stream, session_id="s1", is_disconnected=await gone_after(0), heartbeat_seconds=.02)
        got = [c async for c in it]
    assert got[:2] and all(not c.startswith("id:") for c in got)      # ended promptly, no events


async def test_slow_consumer_drops_oldest_not_blocks(bus):
    stream = SessionEventStream(bus, "s1", maxsize=3)
    async with stream:
        for i in range(10):
            await bus.publish("task.updated", {"session_id": "s1", "n": i})
        await asyncio.sleep(.2)
        seen = []
        while (ev := await stream.next_or_none(.05)) is not None:
            seen.append(ev.data["n"])
    assert seen == [7, 8, 9]


# ───────────────────────── the route ─────────────────────────

class _Row:
    def __init__(self, user_id): self.user_id = user_id


class _FakeDB:
    def __init__(self, owner): self.owner = owner
    def session(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *a): ...
    async def execute(self, stmt): return self
    def scalar_one_or_none(self): return None if self.owner is None else _Row(self.owner)


def ctx(bus, owner=None):
    return SimpleNamespace(orchestrator=SimpleNamespace(event_bus=bus), database=_FakeDB(owner))


class FakeRequest:
    async def is_disconnected(self): return False


async def test_route_streams_events_with_sse_headers(bus):
    resp = await tasks_routes.task_events_sse("s1", FakeRequest(), user_id="u1", context=ctx(bus, owner="u1"))
    assert resp.media_type == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache" and resp.headers["x-accel-buffering"] == "no"
    it = resp.body_iterator
    assert (await it.__anext__()).startswith("retry:")
    assert (await it.__anext__()).startswith(": connected")
    await bus.publish("task.completed", {"session_id": "s1", "task_id": "t9"})
    frame = parse(await asyncio.wait_for(it.__anext__(), 5))
    assert frame["event"] == "task.completed" and frame["data"]["task_id"] == "t9" and frame["id"] == "1"
    await it.aclose()
    assert not any(bus._subscriptions.get(p) for p in ("task.*", "dag.*"))


async def test_route_new_session_allowed_other_users_session_forbidden_no_bus_unavailable(bus):
    await tasks_routes.task_events_sse("brand-new", FakeRequest(), user_id="u1", context=ctx(bus, owner=None))
    with pytest.raises(HTTPException) as e:
        await tasks_routes.task_events_sse("s1", FakeRequest(), user_id="u2", context=ctx(bus, owner="u1"))
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        await tasks_routes.task_events_sse("s1", FakeRequest(), user_id="u1", context=ctx(None, owner="u1"))
    assert e.value.status_code == 503


# ───────────────────────── auth middleware scoping ─────────────────────────

@pytest_asyncio.fixture
async def authed_client():
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get(f"{API_PREFIX}/tasks/{{sid}}/events")
    async def events(sid: str, request: Request):
        return {"user": request.state.user_id}

    @app.get(f"{API_PREFIX}/tasks")
    async def listing(request: Request):
        return {"user": request.state.user_id}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_query_token_accepted_only_on_the_sse_path(authed_client):
    tok = create_access_token("u1")
    ev = f"{API_PREFIX}/tasks/s1/events"
    assert (await authed_client.get(ev)).status_code == 401                              # no token
    r = await authed_client.get(ev, params={"token": tok}); assert r.status_code == 200 and r.json() == {"user": "u1"}
    r = await authed_client.get(ev, headers={"Authorization": f"Bearer {tok}"}); assert r.status_code == 200
    assert (await authed_client.get(ev, params={"token": "garbage"})).status_code == 401
    # a query token must NOT work anywhere else
    assert (await authed_client.get(f"{API_PREFIX}/tasks", params={"token": tok})).status_code == 401
    assert (await authed_client.get(f"{API_PREFIX}/tasks", headers={"Authorization": f"Bearer {tok}"})).status_code == 200
