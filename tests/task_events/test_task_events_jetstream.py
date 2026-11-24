"""JetStream backend against a REAL NATS server (docker). Skipped when docker/nats is unavailable."""
import asyncio
import shutil
import subprocess
import uuid

import pytest
import pytest_asyncio

from pai.config.models.task_events import JetStreamLogConfig, TaskEventsBackend, TaskEventsConfig
from pai.event_bus.task_log import InMemoryTaskEventLog, JetStreamTaskEventLog, build_task_event_log

IMAGE = "nats:2.10-alpine"


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_ok(), reason=f"docker image {IMAGE} not available")


@pytest.fixture(scope="module")
def nats_url():
    name = f"pai-test-nats-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::4222", IMAGE, "-js"],
                   check=True, capture_output=True, timeout=60)
    try:
        port = subprocess.run(["docker", "port", name, "4222/tcp"], check=True, capture_output=True,
                              text=True, timeout=10).stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        import socket, time
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", int(port)), timeout=.2).close()
                break
            except OSError:
                time.sleep(.1)
        time.sleep(.5)
        yield f"nats://127.0.0.1:{port}"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


def cfg(**kw) -> JetStreamLogConfig:
    return JetStreamLogConfig(stream=f"T_{uuid.uuid4().hex[:8]}", subject_prefix=f"t{uuid.uuid4().hex[:6]}", **kw)


@pytest_asyncio.fixture
async def log(nats_url):
    lg = JetStreamTaskEventLog(cfg(), [nats_url])
    await lg.start()
    yield lg
    await lg.close()


async def test_append_replay_and_isolation(log):
    a1 = await log.append("alice", "task.created", {"n": 1})
    await log.append("bob", "task.created", {"secret": "B"})
    a2 = await log.append("alice", "task.started", {"n": 2})
    a3 = await log.append("alice", "task.completed", {"n": 3})
    assert a1 < a2 < a3                                           # increasing (not necessarily contiguous)
    r = await log.replay("alice", a1)
    assert [e.event for e in r.events] == ["task.started", "task.completed"] and not r.gap
    assert [e.seq for e in r.events] == [a2, a3] and r.latest_seq == a3
    full = await log.replay("alice", 0)
    assert [e.seq for e in full.events] == [a1, a2, a3]
    assert "B" not in str([e.data for e in full.events])
    assert (await log.replay("alice", a3)).events == []
    assert await log.latest_seq("nobody") == 0


async def test_live_subscription_gets_only_new_events_for_that_user(log):
    await log.append("alice", "old", {})
    sub = await log.subscribe("alice")
    await log.append("bob", "other", {})
    seq = await log.append("alice", "task.started", {"x": 1})
    ev = await sub.get(3)
    assert ev is not None and ev.event == "task.started" and ev.seq == seq
    assert await sub.get(.3) is None
    await sub.close()


async def test_gap_detected_after_retention_removed_events(nats_url):
    lg = JetStreamTaskEventLog(cfg(max_msgs_per_user=10), [nats_url])
    await lg.start()
    try:
        seqs = [await lg.append("alice", "e", {"i": i}) for i in range(25)]
        assert (await lg.replay("alice", seqs[1])).gap            # events between were trimmed
        assert not (await lg.replay("alice", seqs[-4])).gap        # still retained
        assert (await lg.replay("alice", seqs[-1] + 1000)).gap     # unknown future seq
    finally:
        await lg.close()


async def test_events_survive_a_new_connection(nats_url):
    c = cfg()
    first = JetStreamTaskEventLog(c, [nats_url]); await first.start()
    s1 = await first.append("alice", "task.created", {})
    await first.close()
    second = JetStreamTaskEventLog(c, [nats_url]); await second.start()      # "backend restart"
    s2 = await second.append("alice", "task.started", {})
    r = await second.replay("alice", s1)
    assert [e.seq for e in r.events] == [s2] and not r.gap
    await second.close()


async def test_factory_uses_jetstream_and_falls_back_to_memory_when_unreachable(nats_url):
    conf = TaskEventsConfig(backend=TaskEventsBackend.JETSTREAM, jetstream=cfg())
    lg = await build_task_event_log(conf, [nats_url])
    assert isinstance(lg, JetStreamTaskEventLog)
    await lg.close()
    conf = TaskEventsConfig(backend=TaskEventsBackend.JETSTREAM,
                            jetstream=cfg(connect_timeout_seconds=0.5))
    fallback = await build_task_event_log(conf, ["nats://127.0.0.1:1"])
    assert isinstance(fallback, InMemoryTaskEventLog)
