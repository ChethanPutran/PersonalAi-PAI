"""The agent runtime inside the REAL app: wiring, planner visibility, and real orchestrator calls."""
import sys
import time
from pathlib import Path

import pytest
from agent_fakes import ScriptedLLM, act, fin
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "leaks"))
from helpers import isolated_config  # noqa: E402

from pai.agent.errors import AgentSpawnError  # noqa: E402
from pai.agent.models import AgentEventKind, AgentSpec, AgentState  # noqa: E402
from pai.agent.sources import OrchestratorCapabilitySource  # noqa: E402
from pai.agent.tools import resolve_tool_patterns  # noqa: E402
from pai.server.factory import create_application  # noqa: E402

USER = "agent-it-user"


@pytest.fixture(scope="module")
def booted(tmp_path_factory):
    app = create_application(isolated_config(tmp_path_factory.mktemp("agent_app")))
    with TestClient(app) as client:                      # lifespan: real managers, DB, executors
        yield client, app.state.context
    assert not app.state.context.agents.is_initialized()  # shutdown stopped the runtime


def wait_state(ctx, agent_id, states, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        run = ctx.agents.get(USER, agent_id)
        if run.state in states:
            return run
        time.sleep(0.05)
    raise AssertionError(f"agent stuck in {run.state}; error={run.error}")


def test_runtime_and_routes_are_wired(booted):
    client, ctx = booted
    assert ctx.agents is not None and ctx.agents.is_initialized()
    assert "/api/v1/agents" in client.get("/openapi.json").json()["paths"]


def test_planner_sees_agent_capabilities_but_agents_cannot_get_them(booted):
    client, ctx = booted
    router = ctx.orchestrator.capability_router
    caps = client.portal.call(router.describe_capabilities_with_schema, USER)
    assert {"agent.start", "agent.message", "agent.status", "agent.cancel", "agent.list"} <= set(caps)
    assert "goal" in caps["agent.start"]["parameters"]["properties"]

    source_caps = client.portal.call(OrchestratorCapabilitySource(ctx.orchestrator).describe, USER)
    assert "agent.start" in source_caps and "task.list" in source_caps
    granted = resolve_tool_patterns(["*"], source_caps)
    assert "task.list" in granted and not any(c.startswith(("agent.", "chat.")) for c in granted)
    with pytest.raises(AgentSpawnError):
        resolve_tool_patterns(["agent.start"], source_caps)


def test_agent_actions_run_through_the_real_orchestrator(booted):
    client, ctx = booted
    ctx.agents._loop._llm = ScriptedLLM(act("task.list", "first look"), act("task.list", "look again"),
                                         fin("checked twice"))
    info = client.portal.call(lambda: ctx.agents.spawn(USER, AgentSpec(goal="list my tasks", tools=["task.*"])))
    run = wait_state(ctx, info.id, {AgentState.COMPLETED, AgentState.FAILED})
    assert run.state is AgentState.COMPLETED, run.error and run.error

    obs = [e for e in run.log.snapshot() if e.kind is AgentEventKind.OBSERVATION]
    assert len(obs) == 2 and all(o.data["ok"] for o in obs), [o.data for o in obs]
    assert '"tasks"' in obs[0].data["preview"] and "TaskResult" not in obs[0].data["preview"]
    assert "executor_id" not in obs[0].data["preview"]          # the data, not a repr of the whole result object

    # Two IDENTICAL actions became two separate real tasks. Had the call ids repeated, the
    # orchestrator's idempotency would have replayed the first result instead of acting again.
    tasks = client.portal.call(lambda: ctx.orchestrator.task_manager.list(user_id=USER, limit=50))
    assert sum(1 for t in tasks if t.capability == "task.list") == 2
