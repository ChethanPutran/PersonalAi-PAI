"""Admin tests run the REAL app (real lifespan, DB, managers) fully isolated in a temp dir.

NOTE: Config reads only YAML — env vars such as DATABASE__URL are ignored — so isolation is done
by overriding the Config object's paths directly. Nothing here may touch ./data.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Iterator

from fastapi.testclient import TestClient

from pai.config.app import Config
from pai.config.models.voice import STTProviderName, TTSProviderName
from pai.server.factory import create_application

from sqlalchemy import event as _sa_event
from sqlalchemy.engine import Engine as _Engine


@_sa_event.listens_for(_Engine, "connect")
def _sqlite_wal_for_tests(dbapi_conn, _record) -> None:
    """TEST-ONLY. The production engine uses SQLite's default rollback journal, under which
    concurrent DAG-node writes intermittently fail with "database is locked" (reported to the
    owner of storage/database.py). WAL + busy_timeout on the temp DBs keeps these tests about
    the admin features rather than about that."""
    if "sqlite3" in type(dbapi_conn).__module__ or "aiosqlite" in type(dbapi_conn).__module__ or hasattr(dbapi_conn, "_connection"):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
        finally:
            cur.close()


ADMIN_EMAIL, USER_EMAIL, PASSWORD = "admin@example.com", "user@example.com", "password123"


def isolated_config(tmp: Path, *, test_capabilities: bool = True, history: int = 200) -> Config:
    cfg = Config()
    cfg.database.url = f"sqlite+aiosqlite:///{tmp / 'pai.db'}"
    cfg.memory.long_term.db_url = f"sqlite+aiosqlite:///{tmp / 'long_term.db'}"
    cfg.memory.episodic.db_url = f"sqlite+aiosqlite:///{tmp / 'episodic.db'}"
    cfg.memory.procedural.path = str(tmp / "procedural.json")
    cfg.memory.vector_store.enabled = False            # no embedding model download in tests
    cfg.memory.knowledge_graph.enabled = False
    cfg.logging.log_dir = tmp / "logs"
    cfg.tracing.folder = str(tmp / "traces")
    cfg.tracing.persist_dir = str(tmp / "traces")          # the TraceStore reads persist_dir (default: the REAL data/traces)
    cfg.voice.stt, cfg.voice.tts = STTProviderName.FAKE, TTSProviderName.FAKE
    cfg.voice.models_dir = str(tmp / "voice_models")
    cfg.llm_cost.ledger_path = str(tmp / "llm_usage.jsonl")      # never write into the real ./data
    cfg.admin.history_dir = str(tmp / "admin_history")
    cfg.llm_cost.credits_path = str(tmp / "llm_credits.json")
    cfg.llm_cost.initial_credits_usd = None                      # tests must not inherit the real balance from config.yaml
    cfg.admin.test_capabilities = test_capabilities
    cfg.admin.dag_history_size = history
    return cfg


class AdminHarness:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.app = client.app
        self.ctx = client.app.state.context
        self.admin = self._login(ADMIN_EMAIL)
        self.user = self._login(USER_EMAIL)          # registered second -> not admin

    def _login(self, email: str) -> dict:
        self.client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
        r = self.client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        body = r.json()
        return {"id": body["user_id"], "is_admin": body["is_admin"], "h": {"Authorization": "Bearer " + body["access_token"]}}

    @property
    def H(self) -> dict:
        return self.admin["h"]

    def get(self, path: str, **kw):
        return self.client.get("/api/v1/admin" + path, headers=self.H, **kw)

    def post(self, path: str, **kw):
        return self.client.post("/api/v1/admin" + path, headers=self.H, **kw)

    def run_dag(self, tasks: list[dict], label: str = "t") -> str:
        r = self.post("/run/dag", json={"tasks": tasks, "label": label})
        assert r.status_code == 202, r.text
        return r.json()["request_id"]

    def wait_dag(self, rid: str, *, until: Callable[[dict], bool] = lambda s: s["status"] != "running",
                 timeout: float = 15.0) -> dict:
        import time
        end = time.time() + timeout
        snap: dict = {}
        while time.time() < end:
            r = self.get(f"/dags/{rid}")
            if r.status_code == 200:
                snap = r.json()
                if until(snap):
                    return snap
            time.sleep(0.05)
        raise AssertionError(f"DAG {rid} did not reach the expected state; last={snap}")

    def audit_events(self):
        repo = self.ctx.orchestrator.security_manager.audit_logger.repository
        return [e for e in repo.events if e.event_type.value == "admin_action"]


def sleep_node(i: str, seconds: float, deps=(), **kw) -> dict:
    return {"id": i, "capability": "admin.test.sleep", "parameters": {"seconds": seconds}, "depends_on": list(deps), **kw}


def echo_node(i: str, deps=(), msg="hi") -> dict:
    return {"id": i, "capability": "admin.test.echo", "parameters": {"message": msg}, "depends_on": list(deps)}


# ---------------------------------------------------------------- real-socket server helpers
import signal as _signal
import socket as _socket
import subprocess as _subprocess
import threading as _threading


def free_port() -> int:
    with _socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_isolated_server(data: Path) -> dict:
    """Spawn serve_isolated.py (temp DB, fake voice) and wait for READY. Returns url/proc/log."""
    port = free_port()
    log = open(data / "server.log", "w")
    proc = _subprocess.Popen([sys.executable, str(Path(__file__).parent / "serve_isolated.py"), "--port", str(port),
                              "--data-dir", str(data)], stdout=_subprocess.PIPE, stderr=log, text=True)
    ready = _threading.Event()

    def pump():
        for line in proc.stdout:
            if line.startswith("READY"):
                ready.set()
    _threading.Thread(target=pump, daemon=True).start()
    assert ready.wait(90), "server did not become ready:\n" + (data / "server.log").read_text()[-2000:]
    return {"url": f"http://127.0.0.1:{port}", "proc": proc, "log": data / "server.log"}


def stop_server(info: dict) -> None:
    proc = info["proc"]
    if proc.poll() is None:
        proc.send_signal(_signal.SIGTERM)
        try:
            proc.wait(30)
        except _subprocess.TimeoutExpired:
            proc.kill()


# ----------------------------------------------------------------------------- trace seeding
def seed_traces(store) -> list[str]:
    """Append realistic traces (orchestrator run -> planner LLM call -> tool calls, incl. a failure and an
    in-flight one) to a TraceStore so the Traces tab has data without any LLM provider. Returns trace ids."""
    import time as _t
    import uuid as _u

    from pai.observability.events import TraceEvent

    now = _t.time()
    user = "11111111-aaaa-4bbb-8ccc-222222222222"

    def trace(name, goal, offset, spans, status="completed", live=False):
        tid = _u.uuid4().hex[:16]
        root = _u.uuid4().hex[:12]
        t0 = now - offset
        ev = [TraceEvent(tid, root, None, "trace.start", name, t0, attributes={"user_id": user, "goal": goal})]
        end_ts = t0
        for sp in spans:
            sid = _u.uuid4().hex[:12]
            st = t0 + sp["at"]
            ev.append(TraceEvent(tid, sid, root, "span.start", sp["name"], st, attributes={"user_id": user}))
            for pt in sp.get("points", []):
                k = pt["kind"]
                if k == "llm":
                    base = {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}
                    ev.append(TraceEvent(tid, sid, root, "llm.request", pt["name"], st + pt["at"], attributes={**base, "prompt": pt["prompt"]}))
                    ev.append(TraceEvent(tid, sid, root, "llm.response", pt["name"], st + pt["at"] + pt["ms"] / 1000, duration_ms=pt["ms"],
                                         attributes={**base, "response": pt["response"], "usage": {"prompt_tokens": 842, "completion_tokens": 131, "total_tokens": 973}}))
                elif k == "tool":
                    call = {"capability": pt["cap"], "plugin": pt.get("plugin", "builtin"), "device_id": "pixel-8", "parameters": pt.get("params", {})}
                    ev.append(TraceEvent(tid, sid, root, "tool.call", pt["name"], st + pt["at"], attributes=call))
                    ev.append(TraceEvent(tid, sid, root, "tool.result", pt["name"], st + pt["at"] + pt["ms"] / 1000, duration_ms=pt["ms"], error=pt.get("error"),
                                         attributes={**call, "parameters": {}, "result": pt.get("result", {"ok": True})}))
                elif k == "error":
                    ev.append(TraceEvent(tid, sid, root, "error", pt["name"], st + pt["at"], error=pt["error"], attributes={}))
                elif k == "log":
                    ev.append(TraceEvent(tid, sid, root, "log", pt["name"], st + pt["at"], attributes={"message": pt["message"]}))
            ev.append(TraceEvent(tid, sid, root, "span.end", sp["name"], st + sp["ms"] / 1000, duration_ms=sp["ms"], attributes={}))
            end_ts = max(end_ts, st + sp["ms"] / 1000)
        if not live:
            ev.append(TraceEvent(tid, root, None, "trace.end", name, end_ts + 0.01, duration_ms=(end_ts + 0.01 - t0) * 1000, attributes={"status": status}))
        for e in ev:
            store.append(e)
        return tid

    ids = [
        trace("orchestrator.run", "Take a photo and send it to Mom", 540, [
            {"name": "orchestrator.device_snapshot", "at": 0.01, "ms": 4, "points": []},
            {"name": "planner.decompose", "at": 0.02, "ms": 2380, "points": [
                {"kind": "llm", "name": "llm:openrouter:claude-sonnet-4", "at": 0.05, "ms": 2310,
                 "prompt": "SYSTEM: You are the planning component of a personal AI agent.\nConvert the user's goal into a small executable task graph.\n\nUSER GOAL: Take a photo and send it to Mom\nCAPABILITIES: camera.capture, message.send, notification.send",
                 "response": "{\"tasks\": [{\"id\": \"t1\", \"capability\": \"camera.capture\"}, {\"id\": \"t2\", \"capability\": \"message.send\", \"depends_on\": [\"t1\"]}]}"}]},
            {"name": "dag.execute", "at": 2.5, "ms": 3100, "points": [
                {"kind": "tool", "name": "tool:camera.capture", "at": 0.05, "ms": 1480, "cap": "camera.capture", "plugin": "camera", "params": {"facing": "back"}, "result": {"ok": True, "path": "/sdcard/DCIM/IMG_204.jpg"}},
                {"kind": "tool", "name": "tool:message.send", "at": 1.6, "ms": 1320, "cap": "message.send", "plugin": "messaging", "params": {"to": "Mom", "attachment": "IMG_204.jpg"}, "result": {"ok": True, "message_id": "m_9921"}},
                {"kind": "log", "name": "dag.finished", "at": 3.05, "message": "2/2 tasks completed"}]}]),
        trace("orchestrator.run", "Open the image viewer on my laptop", 300, [
            {"name": "planner.decompose", "at": 0.02, "ms": 1710, "points": [
                {"kind": "llm", "name": "llm:openrouter:claude-sonnet-4", "at": 0.04, "ms": 1650, "prompt": "USER GOAL: Open the image viewer on my laptop", "response": "{\"tasks\": [{\"id\": \"t1\", \"capability\": \"image.open\"}]}"}]},
            {"name": "dag.execute", "at": 1.8, "ms": 30050, "points": [
                {"kind": "tool", "name": "tool:image.open", "at": 0.05, "ms": 30000, "cap": "image.open", "plugin": "viewer", "params": {"path": "/home/me/pic.png"}, "error": "Device desk-linux did not respond in 30.0s", "result": {}},
                {"kind": "error", "name": "dag.task_failed", "at": 30.1, "error": "Device desk-linux did not respond in 30.0s"}]}], status="failed"),
        trace("orchestrator.run", "What is on my calendar tomorrow?", 120, [
            {"name": "orchestrator.memory_recall", "at": 0.01, "ms": 38, "points": [{"kind": "log", "name": "memory.recalled", "at": 0.02, "message": "3 episodic items"}]},
            {"name": "chat.reply", "at": 0.06, "ms": 1840, "points": [
                {"kind": "llm", "name": "llm:openrouter:claude-sonnet-4", "at": 0.02, "ms": 1790, "prompt": "USER: What is on my calendar tomorrow?", "response": "You have a 10:00 stand-up and a 14:30 dentist appointment."}]}]),
        trace("orchestrator.run", "Summarise my unread email", 6, [
            {"name": "planner.decompose", "at": 0.02, "ms": 900, "points": [{"kind": "llm", "name": "llm:openrouter:claude-sonnet-4", "at": 0.03, "ms": 850, "prompt": "USER GOAL: Summarise my unread email", "response": "{\"tasks\": []}"}]}],
              live=True),
    ]
    return ids
