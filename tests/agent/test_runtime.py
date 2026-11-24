"""AgentRuntime: spawning, limits, ownership, steering, shutdown, and the planner-facing capabilities."""
import asyncio
from types import SimpleNamespace

import pytest
from agent_fakes import FakeSource, ScriptedLLM, act, fin, finished, make_runtime, until

from pai.agent.builtins import build_agent_capabilities
from pai.agent.errors import AgentLimitError, AgentNotFound, AgentSpawnError, AgentStateError
from pai.agent.models import AgentEventKind, AgentSpec, AgentState
from pai.agent.runtime import AgentRuntime, RuntimeLimits

A, B = "alice", "bob"


def spec(**kw) -> AgentSpec:
    return AgentSpec(goal=kw.pop("goal", "do the thing"), tools=kw.pop("tools", ["browser.*"]), **kw)


class Blocker:
    """A tool handler that parks until released (an agent stuck in a long action)."""
    def __init__(self):
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def __call__(self, cap, params):
        self.started.set()
        await self.gate.wait()
        return {"ok": True}


def blocked_runtime(n_replies=1, **kw):
    b = Blocker()
    llm = ScriptedLLM(*([act("browser.read")] * n_replies), fin("end"))
    return make_runtime(llm, FakeSource(b), **kw), b, llm


# ── spawning ────────────────────────────────────────────────────────────────

async def test_spawn_resolves_tools_and_lists_only_the_owners_agents():
    rt, b, _ = blocked_runtime()
    info = await rt.spawn(A, spec(tools=["browser.*"]))
    assert info.tools == ["browser.open", "browser.read"] and info.id.startswith("agt_")
    assert [i.id for i in rt.list(A)] == [info.id] and rt.list(B) == []
    b.gate.set()


async def test_spawn_with_bad_tools_creates_nothing():
    rt = make_runtime(ScriptedLLM())
    for tools in (["camera.capture"], ["agent.start"], ["agent.*"], ["chat.reply"], ["nothing.*"]):
        with pytest.raises(AgentSpawnError):
            await rt.spawn(A, spec(tools=tools))
    assert rt.list(A) == []


async def test_another_users_agent_is_indistinguishable_from_a_missing_one():
    rt, b, _ = blocked_runtime()
    info = await rt.spawn(A, spec())
    for call in (lambda: rt.get(B, info.id), lambda: rt.info(B, info.id), lambda: rt.events(B, info.id),
                 lambda: rt.log(B, info.id), lambda: rt.get(A, "agt_nope")):
        with pytest.raises(AgentNotFound):
            call()
    for coro in (rt.send(B, info.id, "hi"), rt.pause(B, info.id), rt.resume(B, info.id), rt.cancel(B, info.id)):
        with pytest.raises(AgentNotFound):
            await coro
    assert rt.get(A, info.id).state is not AgentState.CANCELLED          # bob's cancel did nothing
    b.gate.set()


async def test_limits_per_user_and_total():
    rt, b, _ = blocked_runtime(n_replies=10, limits=RuntimeLimits(max_per_user=2, max_total=3))
    await rt.spawn(A, spec())
    await rt.spawn(A, spec())
    with pytest.raises(AgentLimitError, match="already have 2"):
        await rt.spawn(A, spec())
    await rt.spawn(B, spec())                                            # other users are unaffected
    with pytest.raises(AgentLimitError, match="as many agents"):
        await rt.spawn("carol", spec())
    b.gate.set()


async def test_finished_agents_free_their_slot():
    rt = make_runtime(ScriptedLLM(fin(), fin()), limits=RuntimeLimits(max_per_user=1))
    first = await rt.spawn(A, spec())
    await finished(rt, A, first.id)
    second = await rt.spawn(A, spec())                                   # would raise if the slot were still held
    await finished(rt, A, second.id)


async def test_concurrent_spawns_cannot_overshoot_the_limit():
    rt, b, _ = blocked_runtime(n_replies=20, limits=RuntimeLimits(max_per_user=3))
    results = await asyncio.gather(*[rt.spawn(A, spec()) for _ in range(8)], return_exceptions=True)
    assert sum(1 for r in results if not isinstance(r, Exception)) == 3
    assert all(isinstance(r, AgentLimitError) for r in results if isinstance(r, Exception))
    b.gate.set()


# ── steering ────────────────────────────────────────────────────────────────

async def test_cancel_a_running_agent():
    rt, b, _ = blocked_runtime()
    info = await rt.spawn(A, spec())
    await asyncio.wait_for(b.started.wait(), 2)                          # stuck inside a tool
    out = await rt.cancel(A, info.id)
    run = rt.get(A, info.id)
    assert out.state is AgentState.CANCELLED and run.task.done() and run.log.closed
    assert run.finished_at is not None


async def test_cancel_an_agent_waiting_for_the_user():
    rt = make_runtime(ScriptedLLM(act("ask_user", question="?")))
    info = await rt.spawn(A, spec())
    await until(lambda: rt.get(A, info.id).state is AgentState.WAITING_USER)
    assert (await rt.cancel(A, info.id)).state is AgentState.CANCELLED


async def test_cancel_twice_is_harmless():
    rt = make_runtime(ScriptedLLM(fin()))
    info = await rt.spawn(A, spec())
    await finished(rt, A, info.id)
    assert (await rt.cancel(A, info.id)).state is AgentState.COMPLETED   # a finished run stays finished


async def test_pause_holds_the_next_step_until_resumed():
    rt, b, llm = blocked_runtime(n_replies=1)
    info = await rt.spawn(A, spec())
    await asyncio.wait_for(b.started.wait(), 2)
    assert (await rt.pause(A, info.id)).state is AgentState.PAUSED
    b.gate.set()                                                        # the tool finishes while paused
    await asyncio.sleep(0.15)
    assert len(llm.calls) == 1 and rt.get(A, info.id).state is AgentState.PAUSED   # no new step started
    assert (await rt.resume(A, info.id)).state is AgentState.RUNNING
    run = await finished(rt, A, info.id)
    assert run.state is AgentState.COMPLETED and len(llm.calls) == 2


async def test_pause_and_resume_are_idempotent_and_refused_when_finished():
    rt = make_runtime(ScriptedLLM(fin()))
    info = await rt.spawn(A, spec())
    await finished(rt, A, info.id)
    for coro in (rt.pause(A, info.id), rt.resume(A, info.id)):
        with pytest.raises(AgentStateError):
            await coro


async def test_messages_to_a_finished_agent_are_refused():
    rt = make_runtime(ScriptedLLM(fin()))
    info = await rt.spawn(A, spec())
    await finished(rt, A, info.id)
    with pytest.raises(AgentStateError):
        await rt.send(A, info.id, "hello?")


async def test_empty_and_overlong_messages_and_inbox_overflow():
    rt, b, _ = blocked_runtime()
    info = await rt.spawn(A, spec())
    await asyncio.wait_for(b.started.wait(), 2)
    with pytest.raises(ValueError):
        await rt.send(A, info.id, "   ")
    await rt.send(A, info.id, "x" * 10_000)
    sent = [e for e in rt.events(A, info.id) if e.kind is AgentEventKind.USER][0]
    assert len(sent.data["text"]) == 4000                                # clipped
    for _ in range(49):
        await rt.send(A, info.id, "more")
    with pytest.raises(AgentLimitError):
        await rt.send(A, info.id, "one too many")
    b.gate.set()


# ── events ──────────────────────────────────────────────────────────────────

async def test_event_replay_and_live_stream_end_when_the_agent_finishes():
    rt = make_runtime(ScriptedLLM(act("browser.read", "look"), fin("ok")))
    info = await rt.spawn(A, spec())
    streamed = [e async for e in rt.stream(A, info.id)]                  # live, until the run is over
    seqs = [e.seq for e in streamed]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert [e.kind for e in streamed][:1] == [AgentEventKind.STATE]
    assert {AgentEventKind.THOUGHT, AgentEventKind.ACTION, AgentEventKind.OBSERVATION,
            AgentEventKind.FINAL} <= {e.kind for e in streamed}
    missed = [e async for e in rt.stream(A, info.id, after=seqs[2])]     # a reconnect replays only what was missed
    assert [e.seq for e in missed] == seqs[3:]
    assert [e.seq for e in rt.events(A, info.id, after=seqs[-2])] == seqs[-1:]


async def test_next_batch_times_out_without_breaking_the_log():
    rt, b, _ = blocked_runtime()
    info = await rt.spawn(A, spec())
    await asyncio.wait_for(b.started.wait(), 2)
    log = rt.log(A, info.id)
    cursor = log.last_seq
    assert await log.next_batch(cursor, timeout=0.05) == []              # keep-alive tick
    await rt.send(A, info.id, "hi")
    batch = await log.next_batch(cursor, timeout=1)
    assert batch and batch[0].kind is AgentEventKind.USER
    b.gate.set()


async def test_bus_events_carry_no_tool_arguments():
    rt = make_runtime(ScriptedLLM(act("browser.open", url="https://secret.test/?token=abc"), fin("done")))
    seen = []
    rt._emit = lambda name, data=None, **kw: seen.append((name, data))
    info = await rt.spawn(A, spec())
    await finished(rt, A, info.id)
    names = {n for n, _ in seen}
    assert {"agent.state", "agent.final"} <= names and "agent.action" not in names
    assert all("args" not in d and d["user_id"] == A for _, d in seen)


# ── robustness ──────────────────────────────────────────────────────────────

async def test_a_crashing_run_still_ends_in_a_terminal_state():
    def boom():
        raise RuntimeError("template exploded")
    rt = AgentRuntime(capability_source=FakeSource(), llm=ScriptedLLM(), prompt=boom)
    info = await rt.spawn(A, spec())
    run = await finished(rt, A, info.id)
    assert run.state is AgentState.FAILED and run.error.startswith("internal_error") and run.log.closed


async def test_shutdown_cancels_everything_still_running():
    rt, b, _ = blocked_runtime(n_replies=5, limits=RuntimeLimits(max_per_user=5))
    ids = [(await rt.spawn(A, spec())).id for _ in range(3)]
    await asyncio.wait_for(b.started.wait(), 2)
    await rt.shutdown()
    assert [rt.get(A, i).state for i in ids] == [AgentState.CANCELLED] * 3
    assert all(rt.get(A, i).task.done() for i in ids)


async def test_finished_runs_are_reaped_but_running_ones_never_are():
    rt = make_runtime(ScriptedLLM(fin(), fin(), fin()), limits=RuntimeLimits(retain_finished=1, max_per_user=5))
    ids = []
    for _ in range(3):
        info = await rt.spawn(A, spec())
        await finished(rt, A, info.id)
        ids.append(info.id)
    assert len(rt.list(A)) == 1                                          # only the newest finished run is kept
    rt, b, _ = blocked_runtime(limits=RuntimeLimits(finished_ttl_seconds=0))
    info = await rt.spawn(A, spec())
    await asyncio.wait_for(b.started.wait(), 2)
    assert [i.id for i in rt.list(A)] == [info.id]                       # alive: not reaped despite ttl=0
    b.gate.set()


# ── planner-facing capabilities ─────────────────────────────────────────────

def caps(rt):
    return {c.id: c for c in build_agent_capabilities(rt)}


TASK = SimpleNamespace(session_id="sess-9")


async def test_agent_start_via_the_builtin_capability():
    rt, b, _ = blocked_runtime()
    c = caps(rt)
    assert set(c) == {"agent.start", "agent.message", "agent.status", "agent.cancel", "agent.list"}
    out = await c["agent.start"].handler(
        {"goal": "browse for a playlist", "tools": "browser.open, browser.read", "max_steps": 7},
        user_id=A, task=TASK)
    assert out["state"] in ("pending", "running") and out["tools"] == ["browser.open", "browser.read"]
    run = rt.get(A, out["agent_id"])
    assert run.session_id == "sess-9" and run.spec.limits.max_steps == 7
    b.gate.set()


async def test_agent_start_validation_errors_are_readable():
    rt = make_runtime(ScriptedLLM())
    start = caps(rt)["agent.start"].handler
    with pytest.raises(ValueError, match="needs a goal"):
        await start({"tools": ["browser.*"]}, user_id=A)
    with pytest.raises(ValueError, match="no tools selected|invalid agent request"):
        await start({"goal": "x"}, user_id=A)
    with pytest.raises(ValueError, match="not an available capability"):
        await start({"goal": "x", "tools": ["camera.capture"]}, user_id=A)
    with pytest.raises(ValueError, match="not allowed for agents"):
        await start({"goal": "x", "tools": ["agent.start"]}, user_id=A)
    with pytest.raises(ValueError, match="invalid agent request"):
        await start({"goal": "x", "tools": ["browser.*"], "max_steps": 9999}, user_id=A)


async def test_message_status_cancel_list_capabilities_are_user_scoped():
    rt, b, _ = blocked_runtime()
    c = caps(rt)
    aid = (await c["agent.start"].handler({"goal": "g", "tools": ["browser.*"]}, user_id=A))["agent_id"]
    await asyncio.wait_for(b.started.wait(), 2)
    assert (await c["agent.message"].handler({"agent_id": aid, "text": "hi"}, user_id=A)) == {"delivered": True}
    status = await c["agent.status"].handler({"agent_id": aid, "events": 3}, user_id=A)
    assert status["id"] == aid and 0 < len(status["recent_events"]) <= 3
    assert [x["id"] for x in (await c["agent.list"].handler({}, user_id=A))["agents"]] == [aid]
    assert (await c["agent.list"].handler({}, user_id=B))["agents"] == []
    for name, params in (("agent.message", {"agent_id": aid, "text": "x"}), ("agent.status", {"agent_id": aid}),
                         ("agent.cancel", {"agent_id": aid})):
        with pytest.raises(ValueError, match="not found"):
            await c[name].handler(params, user_id=B)
    assert (await c["agent.cancel"].handler({"agent_id": aid}, user_id=A))["state"] == "cancelled"
