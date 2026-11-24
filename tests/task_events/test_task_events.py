"""Per-user task event stream: the log (memory), SSE framing/replay, route auth + isolation,
and the bus -> log publisher."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException

from pai.api.middleware.auth import AuthMiddleware
from pai.api.routes import tasks as tasks_routes
from pai.api.routes.task_stream import parse_resume_point, user_task_sse
from pai.common.constants import API_PREFIX
from pai.config.models.event_bus import EventBusConfig
from pai.event_bus.bus import EventBus
from pai.event_bus.task_log import InMemoryTaskEventLog
from pai.orchestration.task_event_publisher import TaskEventPublisher
from pai.security.tokens.auth import create_access_token
from pai.tasks.models import Task, TaskStatus, TaskType


def parse(frame: str) -> dict:
    out = {}
    for line in frame.strip().splitlines():
        k, _, v = line.partition(": ")
        out[k] = json.loads(v) if k == "data" else v
    return out


async def collect(gen, n_frames: int, timeout=5):
    """Collect frames until `n` id-carrying frames arrived."""
    out = []
    async def run():
        async for c in gen:
            out.append(c)
            if sum(1 for x in out if x.startswith("id:")) >= n_frames:
                return
    await asyncio.wait_for(run(), timeout)
    await gen.aclose()
    return [parse(c) for c in out if c.startswith("id:")]


async def never(): return False


# ───────────────────────────── the log ─────────────────────────────

async def test_seq_is_per_user_and_replay_returns_only_missed():
    log = InMemoryTaskEventLog()
    assert [await log.append("a", "task.created", {"i": i}) for i in range(3)] == [1, 2, 3]
    assert await log.append("b", "task.created", {}) == 1                 # independent space
    r = await log.replay("a", 1)
    assert [e.seq for e in r.events] == [2, 3] and not r.gap and r.latest_seq == 3
    assert (await log.replay("a", 3)).events == []                         # caught up


async def test_replay_never_returns_another_users_events():
    log = InMemoryTaskEventLog()
    await log.append("a", "task.created", {"secret": "A"})
    await log.append("b", "task.created", {"secret": "B"})
    assert "A" not in json.dumps([e.model_dump() for e in (await log.replay("b", 0)).events])


async def test_gap_when_rolled_off_unknown_or_from_the_future():
    log = InMemoryTaskEventLog(buffer_size=10)
    for i in range(25):
        await log.append("a", "e", {"i": i})
    assert (await log.replay("a", 2)).gap                                  # rolled off the ring
    assert (await log.replay("a", 99)).gap                                 # seq space was reset (restart)
    assert (await log.replay("ghost", 5)).gap                              # unknown user, remembers a seq
    assert not (await log.replay("ghost", 0)).gap                          # fresh client
    assert not (await log.replay("a", 24)).gap                             # still inside the ring


async def test_ttl_expires_old_events():
    log = InMemoryTaskEventLog(ttl_seconds=0.05)
    await log.append("a", "e", {})
    await asyncio.sleep(.1)
    await log.append("a", "e", {})
    assert (await log.replay("a", 0)).gap                                  # event 1 expired: not contiguous


async def test_user_count_is_bounded_and_live_subscribers_are_kept():
    log = InMemoryTaskEventLog(max_users=10)
    keeper = await log.subscribe("keeper")
    for i in range(30):
        await log.append(f"u{i}", "e", {})
    stats = log.stats()
    assert stats["users"] <= 10 and stats["subscribers"] == 1
    assert "keeper" in log._users
    await keeper.close()


async def test_slow_subscriber_is_flagged_for_resync_not_grown():
    log = InMemoryTaskEventLog(subscriber_queue=3)
    sub = await log.subscribe("a")
    for i in range(10):
        await log.append("a", "e", {"i": i})
    assert sub.needs_resync and sub._queue.qsize() <= 3
    await sub.close()


async def test_subscription_gets_live_events_and_close_unregisters():
    log = InMemoryTaskEventLog()
    sub = await log.subscribe("a")
    await log.append("a", "task.started", {"x": 1})
    ev = await sub.get(1)
    assert ev.event == "task.started" and ev.seq == 1
    assert await sub.get(.05) is None
    await sub.close()
    assert log.stats()["subscribers"] == 0


# ───────────────────────────── the SSE body ─────────────────────────────

def test_resume_point_parsing():
    assert parse_resume_point(None, None) is None
    assert parse_resume_point("7", None) == 7
    assert parse_resume_point("7", 3) == 7                                 # header wins
    assert parse_resume_point(None, 4) == 4
    assert parse_resume_point("junk", None) is None


async def test_fresh_connection_gets_ready_then_live_events_with_ids():
    log = InMemoryTaskEventLog()
    await log.append("a", "task.created", {"old": True})                   # before connecting: not replayed
    gen = user_task_sse(log, "a", None, is_disconnected=never)
    first = await gen.__anext__()
    assert first.startswith("retry:")
    ready = parse(await gen.__anext__())
    assert ready["event"] == "ready" and ready["data"] == {"seq": 1, "replayed": 0}
    await log.append("a", "task.started", {"id": "t1"})
    ev = parse(await asyncio.wait_for(gen.__anext__(), 2))
    assert (ev["id"], ev["event"], ev["data"]["id"]) == ("2", "task.started", "t1")
    await gen.aclose()
    assert log.stats()["subscribers"] == 0


async def test_reconnect_replays_exactly_the_missed_events_then_goes_live():
    log = InMemoryTaskEventLog()
    for i in range(5):
        await log.append("a", "task.updated", {"i": i})
    gen = user_task_sse(log, "a", 2, is_disconnected=never)
    frames = await collect(gen, 4)                                         # 3,4,5 + ready
    assert [(f["id"], f["event"]) for f in frames] == [("3", "task.updated"), ("4", "task.updated"),
                                                       ("5", "task.updated"), ("5", "ready")]
    assert frames[-1]["data"] == {"seq": 5, "replayed": 3}


async def test_events_during_replay_are_not_duplicated():
    log = InMemoryTaskEventLog()
    for i in range(3):
        await log.append("a", "e", {"i": i})
    gen = user_task_sse(log, "a", 0, is_disconnected=never)
    await gen.__anext__()                                                  # retry (subscribed now)
    await log.append("a", "e", {"i": 3})                                   # lands in queue AND replay
    frames = []
    async for c in gen:
        if c.startswith("id:"):
            frames.append(parse(c))
        if len(frames) >= 5:
            break
    await gen.aclose()
    seqs = [int(f["id"]) for f in frames if f["event"] == "e"]
    assert seqs == [1, 2, 3, 4]


async def test_resync_frame_when_history_is_gone():
    log = InMemoryTaskEventLog(buffer_size=10)
    for i in range(30):
        await log.append("a", "e", {})
    frames = await collect(user_task_sse(log, "a", 3, is_disconnected=never), 2)
    assert [f["event"] for f in frames] == ["resync", "ready"]
    assert frames[0]["data"] == {"seq": 30, "reason": "history_unavailable"}


async def test_resync_frame_for_a_slow_consumer_then_stream_continues():
    log = InMemoryTaskEventLog(subscriber_queue=8)
    gen = user_task_sse(log, "a", None, is_disconnected=never)
    await gen.__anext__(); await gen.__anext__()                           # retry, ready
    for i in range(30):                                                    # client not reading
        await log.append("a", "e", {"i": i})
    f = parse(await asyncio.wait_for(gen.__anext__(), 3))
    assert f["event"] == "resync" and f["data"]["reason"] == "slow_consumer" and f["data"]["seq"] == 30
    await log.append("a", "e", {"i": 99})
    nxt = parse(await asyncio.wait_for(gen.__anext__(), 3))
    assert nxt["id"] == "31"
    await gen.aclose()


async def test_keepalive_comment_when_idle_and_stops_on_shutdown_and_disconnect():
    log = InMemoryTaskEventLog()
    calls = {"n": 0}
    async def gone_after_3():
        calls["n"] += 1
        return calls["n"] > 3
    chunks = [c async for c in user_task_sse(log, "a", None, is_disconnected=gone_after_3, heartbeat_seconds=.03)]
    assert chunks.count(": keep-alive\n\n") >= 2
    stop = asyncio.Event()
    stop.set()
    chunks = [c async for c in user_task_sse(log, "a", None, is_disconnected=never, should_stop=stop.is_set)]
    assert not any(c.startswith(": keep-alive") for c in chunks)           # ended without waiting
    assert log.stats()["subscribers"] == 0


# ───────────────────────────── the route ─────────────────────────────

class FakeRequest:
    def __init__(self, auth="Bearer x"): self.headers = {"authorization": auth} if auth else {}
    async def is_disconnected(self): return False


def ctx(log):
    return SimpleNamespace(task_events=log, shutting_down=asyncio.Event())


async def test_route_streams_only_the_callers_events_with_sse_headers():
    log = InMemoryTaskEventLog()
    context = ctx(log)
    resp = await tasks_routes.user_task_events(FakeRequest(), after=None, last_event_id=None,
                                               user_id="alice", context=context)
    assert resp.media_type == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache" and resp.headers["x-accel-buffering"] == "no"
    it = resp.body_iterator
    await it.__anext__()                                                   # retry
    assert parse(await it.__anext__())["event"] == "ready"
    await log.append("bob", "task.started", {"id": "bobs"})
    await log.append("alice", "task.started", {"id": "alices"})
    ev = parse(await asyncio.wait_for(it.__anext__(), 3))
    assert ev["data"]["id"] == "alices"
    context.shutting_down.set()                                            # server stopping: stream ends
    rest = [c async for c in it]
    assert "bobs" not in "".join(rest)


async def test_route_resumes_from_last_event_id_header_and_after_param():
    log = InMemoryTaskEventLog()
    for i in range(4):
        await log.append("alice", "e", {"i": i})
    for kwargs in ({"last_event_id": "2", "after": None}, {"last_event_id": None, "after": 2}):
        resp = await tasks_routes.user_task_events(FakeRequest(), user_id="alice", context=ctx(log), **kwargs)
        frames = await collect(resp.body_iterator, 3)
        assert [f["id"] for f in frames] == ["3", "4", "4"]


async def test_route_requires_bearer_header_and_a_log():
    log = InMemoryTaskEventLog()
    with pytest.raises(HTTPException) as e:
        await tasks_routes.user_task_events(FakeRequest(auth=None), after=None, last_event_id=None,
                                            user_id="a", context=ctx(log))
    assert e.value.status_code == 401
    with pytest.raises(HTTPException) as e:
        await tasks_routes.user_task_events(FakeRequest(), after=None, last_event_id=None,
                                            user_id="a", context=SimpleNamespace(task_events=None))
    assert e.value.status_code == 503


async def test_route_is_registered_before_task_id_and_rejects_query_tokens():
    paths = [r.path for r in tasks_routes.router.routes]
    assert paths.index("/tasks/events") < paths.index("/tasks/{task_id}")

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.state.context = ctx(InMemoryTaskEventLog())
    app.include_router(tasks_routes.router, prefix=API_PREFIX)
    tok = create_access_token("u1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get(f"{API_PREFIX}/tasks/events")).status_code == 401
        assert (await c.get(f"{API_PREFIX}/tasks/events", params={"token": tok})).status_code == 401


# ───────────────────────────── bus -> log publisher ─────────────────────────────

@pytest_asyncio.fixture
async def wired():
    bus = EventBus(config=EventBusConfig())
    await bus.initialize(); await bus.start()
    log = InMemoryTaskEventLog()
    pub = TaskEventPublisher(event_bus=bus, log=log)
    await pub.start()
    yield bus, log, pub
    await pub.stop(); await bus.shutdown()


async def wait_for_seq(log, user, n):
    for _ in range(100):
        if await log.latest_seq(user) >= n:
            return
        await asyncio.sleep(.02)
    raise AssertionError("event never reached the log")


async def test_task_events_are_recorded_for_the_owner_only_and_serialized_like_the_api(wired):
    bus, log, pub = wired
    t = Task(name="x", capability="camera.stream", type=TaskType.STREAM, user_id="alice", status=TaskStatus.RUNNING)
    await bus.publish("task.started", t.to_dict())
    await wait_for_seq(log, "alice", 1)
    ev = (await log.replay("alice", 0)).events[0]
    assert ev.event == "task.started" and ev.data["id"] == t.id and ev.data["status"] == "running"
    for k in ("controls", "can_pause", "can_resume", "can_cancel", "cancel_outcome", "control_blocked_reason"):
        assert k in ev.data
    assert await log.latest_seq("bob") == 0


async def test_dag_events_are_recorded_stream_frames_and_ownerless_events_are_not(wired):
    bus, log, pub = wired
    await bus.publish("dag.completed", {"user_id": "alice", "run_id": "r1"})
    await bus.publish("task.stream", {"user_id": "alice", "id": "t", "status": "running", "frame": 1})
    await bus.publish("task.updated", {"id": "t", "status": "running"})            # no owner
    await wait_for_seq(log, "alice", 1)
    await asyncio.sleep(.1)
    events = (await log.replay("alice", 0)).events
    assert [e.event for e in events] == ["dag.completed"]
    assert pub.stats.dropped_no_owner == 1
