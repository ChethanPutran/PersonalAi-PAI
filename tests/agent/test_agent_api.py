"""The /agents HTTP + SSE + WebSocket surface (real FastAPI app, scripted LLM)."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from agent_fakes import FakeSource, ScriptedLLM, act, fin, make_runtime
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pai.agent.runtime import RuntimeLimits
from pai.api.routes import agents as agents_routes


def build(llm, source=None, limits=None):
    runtime = make_runtime(llm, source, limits)
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):          # stands in for AuthMiddleware
        request.state.user_id = request.headers.get("X-User")
        return await call_next(request)

    app.include_router(agents_routes.router, prefix="/api/v1")
    enabled = {"agent": True}                          # the built-in agent plugin; tests flip it per case

    async def is_enabled_for_user(user_id, plugin_id):
        return enabled.get(plugin_id, False)

    orchestrator = SimpleNamespace(plugin_manager=SimpleNamespace(is_enabled_for_user=is_enabled_for_user))
    app.state.context = SimpleNamespace(agents=runtime, shutting_down=asyncio.Event(), orchestrator=orchestrator)
    app.state.agent_plugin_enabled = enabled
    return app, runtime


@pytest.fixture(autouse=True)
def ws_token_is_the_user(monkeypatch):
    monkeypatch.setattr(agents_routes, "decode_access_token", lambda t: t if t != "bad" else (_ for _ in ()).throw(ValueError("bad")))


def H(user="alice"):
    return {"X-User": user}


def wait_for(client, agent_id, states, user="alice", timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        info = client.get(f"/api/v1/agents/{agent_id}", headers=H(user)).json()
        if info["state"] in states:
            return info
        time.sleep(0.02)
    raise AssertionError(f"never reached {states}; last: {info}")


START = {"goal": "find a playlist", "tools": ["browser.*"]}


def test_start_runs_to_completion_and_reports_the_result():
    app, _ = build(ScriptedLLM(act("browser.read"), fin("Found: Lofi Beats")))
    with TestClient(app) as c:
        r = c.post("/api/v1/agents", json=START, headers=H())
        assert r.status_code == 201
        body = r.json()
        assert body["tools"] == ["browser.open", "browser.read"] and "user_id" not in body
        done = wait_for(c, body["id"], {"completed"})
        assert done["result"] == "Found: Lofi Beats" and done["steps"] == 2
        assert [a["id"] for a in c.get("/api/v1/agents", headers=H()).json()] == [body["id"]]
        assert c.get("/api/v1/agents", headers=H("bob")).json() == []


@pytest.mark.parametrize("body,code", [
    ({"goal": "x", "tools": ["camera.capture"]}, 422),
    ({"goal": "x", "tools": ["agent.start"]}, 422),
    ({"goal": "", "tools": ["browser.*"]}, 422),
    ({"goal": "x", "tools": []}, 422),
    ({"goal": "x", "tools": ["browser.*"], "max_steps": 1000}, 422),
    ({"goal": "x", "tools": ["browser.*"], "bogus": 1}, 201),   # unknown fields are ignored
])
def test_start_validation(body, code):
    app, _ = build(ScriptedLLM(fin()))
    with TestClient(app) as c:
        assert c.post("/api/v1/agents", json=body, headers=H()).status_code == code


def test_authentication_and_ownership():
    app, _ = build(ScriptedLLM(act("ask_user", question="?")))
    with TestClient(app) as c:
        assert c.post("/api/v1/agents", json=START).status_code == 401
        aid = c.post("/api/v1/agents", json=START, headers=H()).json()["id"]
        for method, path, kw in (("get", "", {}), ("post", "/messages", {"json": {"text": "hi"}}),
                                 ("post", "/cancel", {}), ("post", "/pause", {}), ("get", "/events", {})):
            r = getattr(c, method)(f"/api/v1/agents/{aid}{path}", headers=H("mallory"), **kw)
            assert r.status_code == 404, (method, path)
        assert c.get(f"/api/v1/agents/{aid}", headers=H()).json()["state"] in ("running", "waiting_user", "pending")
        c.post(f"/api/v1/agents/{aid}/cancel", headers=H())


def test_limit_returns_429():
    gate = asyncio.Event()

    async def blocked(cap, params):
        await gate.wait()
    app, _ = build(ScriptedLLM(*[act("browser.read")] * 5), FakeSource(blocked), RuntimeLimits(max_per_user=1))
    with TestClient(app) as c:
        assert c.post("/api/v1/agents", json=START, headers=H()).status_code == 201
        r = c.post("/api/v1/agents", json=START, headers=H())
        assert r.status_code == 429 and "already have 1" in r.json()["detail"]
        assert c.post("/api/v1/agents", json=START, headers=H("bob")).status_code == 201


def test_talk_to_the_agent_while_it_works():
    app, runtime = build(ScriptedLLM(act("ask_user", question="Which playlist?"), fin("playing Jazz")))
    with TestClient(app) as c:
        aid = c.post("/api/v1/agents", json=START, headers=H()).json()["id"]
        waiting = wait_for(c, aid, {"waiting_user"})
        assert waiting["pending_question"] == "Which playlist?"
        r = c.post(f"/api/v1/agents/{aid}/messages", json={"text": "Jazz"}, headers=H())
        assert r.status_code == 202
        assert wait_for(c, aid, {"completed"})["result"] == "playing Jazz"
        assert c.post(f"/api/v1/agents/{aid}/messages", json={"text": "more"}, headers=H()).status_code == 409
        assert c.post(f"/api/v1/agents/{aid}/messages", json={"text": ""}, headers=H()).status_code == 422


def test_cancel_pause_resume_endpoints():
    gate = asyncio.Event()

    async def blocked(cap, params):
        await gate.wait()
    app, _ = build(ScriptedLLM(act("browser.read"), fin()), FakeSource(blocked))
    with TestClient(app) as c:
        aid = c.post("/api/v1/agents", json=START, headers=H()).json()["id"]
        assert c.post(f"/api/v1/agents/{aid}/pause", headers=H()).json()["state"] == "paused"
        assert c.post(f"/api/v1/agents/{aid}/resume", headers=H()).json()["state"] == "running"
        assert c.post(f"/api/v1/agents/{aid}/cancel", headers=H()).json()["state"] == "cancelled"
        for action in ("pause", "resume"):
            assert c.post(f"/api/v1/agents/{aid}/{action}", headers=H()).status_code == 409


def sse_frames(text):
    frames = []
    for block in text.strip().split("\n\n"):
        f = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if f:
            frames.append(f)
    return frames


def test_sse_replays_everything_and_resumes_after_a_seq():
    app, _ = build(ScriptedLLM(act("browser.read", "look"), fin("ok")))
    with TestClient(app) as c:
        aid = c.post("/api/v1/agents", json=START, headers=H()).json()["id"]
        wait_for(c, aid, {"completed"})
        r = c.get(f"/api/v1/agents/{aid}/events", headers=H())
        assert r.headers["content-type"].startswith("text/event-stream")
        frames = sse_frames(r.text)
        ids = [int(f["id"]) for f in frames]
        assert ids == sorted(ids) and {"state", "thought", "action", "observation", "final"} <= {f["event"] for f in frames}
        assert json.loads(frames[0]["data"])["seq"] == ids[0]
        after = c.get(f"/api/v1/agents/{aid}/events?after={ids[3]}", headers=H())
        assert [int(f["id"]) for f in sse_frames(after.text)] == ids[4:]
        hdr = c.get(f"/api/v1/agents/{aid}/events", headers={**H(), "Last-Event-ID": str(ids[-2])})
        assert [int(f["id"]) for f in sse_frames(hdr.text)] == ids[-1:]


def test_websocket_conversation():
    app, _ = build(ScriptedLLM(act("ask_user", question="Which one?"), fin("done: Jazz")))
    with TestClient(app) as c:
        aid = c.post("/api/v1/agents", json=START, headers=H()).json()["id"]
        with c.websocket_connect(f"/api/v1/agents/{aid}/ws?token=alice") as ws:
            seen = []
            while True:
                ev = ws.receive_json()
                seen.append(ev["kind"])
                if ev["kind"] == "question":
                    assert ev["question"] == "Which one?"
                    ws.send_json({"type": "ping"})
                    assert ws.receive_json() == {"kind": "pong"}
                    ws.send_json({"type": "message", "text": "Jazz"})
                if ev["kind"] == "final":
                    assert ev["result"] == "done: Jazz"
                if ev["kind"] == "closed":
                    break
            assert "user" in seen and seen.index("question") < seen.index("user") < seen.index("final")


def test_websocket_errors_do_not_kill_the_connection_and_auth_is_enforced():
    app, _ = build(ScriptedLLM(act("ask_user", question="?")))
    with TestClient(app) as c:
        aid = c.post("/api/v1/agents", json=START, headers=H()).json()["id"]
        with c.websocket_connect(f"/api/v1/agents/{aid}/ws?token=alice") as ws:
            ws.send_json({"type": "bogus"})
            while (ev := ws.receive_json())["kind"] != "error":
                pass
            assert "unknown frame type" in ev["detail"]
            ws.send_json({"type": "message", "text": "   "})
            while (ev := ws.receive_json())["kind"] != "error":
                pass
            ws.send_json({"type": "cancel"})
            while ws.receive_json()["kind"] != "closed":
                pass
        for url, code in ((f"/api/v1/agents/{aid}/ws", 4401), (f"/api/v1/agents/{aid}/ws?token=bad", 4401),
                          (f"/api/v1/agents/{aid}/ws?token=mallory", 4404), ("/api/v1/agents/agt_nope/ws?token=alice", 4404)):
            with pytest.raises(WebSocketDisconnect) as exc:
                with c.websocket_connect(url):
                    pass
            assert exc.value.code == code, url


def test_websocket_reconnect_replays_only_what_was_missed():
    app, _ = build(ScriptedLLM(act("browser.read"), fin("ok")))
    with TestClient(app) as c:
        aid = c.post("/api/v1/agents", json=START, headers=H()).json()["id"]
        wait_for(c, aid, {"completed"})
        with c.websocket_connect(f"/api/v1/agents/{aid}/ws?token=alice") as ws:
            first = []
            while (ev := ws.receive_json())["kind"] != "closed":
                first.append(ev["seq"])
        cut = first[2]
        with c.websocket_connect(f"/api/v1/agents/{aid}/ws?token=alice&after={cut}") as ws:
            again = []
            while (ev := ws.receive_json())["kind"] != "closed":
                again.append(ev["seq"])
        assert again == [s for s in first if s > cut]


def test_starting_an_agent_needs_the_agent_plugin_enabled():
    llm = None
    app, _ = build(llm)
    app.state.agent_plugin_enabled["agent"] = False
    with TestClient(app) as client:
        r = client.post("/api/v1/agents", json={"goal": "x", "tools": ["browser.*"]}, headers=H())
    assert r.status_code == 403 and r.json()["detail"]["code"] == "agent_plugin_disabled"
