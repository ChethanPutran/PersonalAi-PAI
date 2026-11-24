"""The admin panel over a REAL socket: spawn uvicorn on a free port (isolated temp DB), drive it
with httpx — demo DAG end to end, live SSE, static UI + security headers, clean SIGTERM shutdown."""
import re
import signal
import threading
import time
from pathlib import Path

import httpx
import pytest

from admin_helpers import start_isolated_server, stop_server

HERE = Path(__file__).parent
STATIC = HERE.parent.parent / "src" / "pai" / "api" / "admin" / "static"
EMAIL, PASSWORD = "admin@example.com", "password123"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    info = start_isolated_server(tmp_path_factory.mktemp("srv"))
    yield info
    stop_server(info)


@pytest.fixture(scope="module")
def http(server):
    with httpx.Client(base_url=server["url"], timeout=30) as c:
        c.post("/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})
        r = c.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert r.status_code == 200 and r.json()["is_admin"] is True
        c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
        yield c


def wait_dag(http, rid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        s = http.get(f"/api/v1/admin/dags/{rid}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.1)
    raise AssertionError("DAG did not finish")


def test_static_modules_are_served_at_admin_static_with_security_headers(server):
    with httpx.Client(base_url=server["url"]) as anon:                                 # NO token
        files = [p for p in STATIC.rglob("*") if p.is_file()]
        assert len(files) >= 18
        csp = None
        for f in files:
            rel = f.relative_to(STATIC).as_posix()
            resp = anon.get(f"/admin/static/{rel}")
            assert resp.status_code == 200, rel
            csp = csp or resp.headers["content-security-policy"]
            assert resp.headers["content-security-policy"] == csp and resp.headers["x-content-type-options"] == "nosniff"
            if rel.endswith(".js"):
                assert "javascript" in resp.headers["content-type"], rel
            if rel.endswith(".css"):
                assert "text/css" in resp.headers["content-type"], rel
        assert "script-src 'self'" in csp and "default-src 'none'" in csp and "font-src 'self'" in csp
        for font in ("Orbitron-Variable.ttf", "Rajdhani-SemiBold.ttf", "Inter-Variable.ttf", "JetBrainsMono-Variable.ttf", "OFL-orbitron.txt"):
            r = anon.get(f"/admin/static/fonts/{font}")
            assert r.status_code == 200 and int(r.headers["content-length"]) > 1000, font
        # No second admin UI at a competing URL: /admin and /admin/ are not served.
        assert anon.get("/admin", follow_redirects=False).status_code in (401, 404)
        assert anon.get("/admin/", follow_redirects=False).status_code in (401, 404)
        assert anon.get("/admin/static/../admin.html").status_code in (400, 401, 404)       # no traversal to the page source
        assert anon.get("/api/v1/admin/overview").status_code == 401                    # the API itself stays protected


def test_extra_tab_modules_use_no_inline_code_no_innerhtml_and_no_external_hosts():
    for f in list((STATIC / "js").rglob("*.js")) + list((STATIC / "css").glob("*.css")):
        text = f.read_text()
        assert "innerHTML" not in text and "eval(" not in text and "document.write" not in text, f.name
        assert not re.search(r"https?://(?!www\.w3\.org)", text), f"{f.name} references an external host"
        assert "style=\"" not in text and "setAttribute('style'" not in text, f.name
    html = (STATIC / "index.html").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and "<style" not in html and ' style="' not in html


def test_demo_dag_runs_end_to_end_over_real_http_with_live_sse(server, http):
    names, ready = [], threading.Event()

    def listen():
        with httpx.stream("GET", server["url"] + "/api/v1/admin/events", headers={"Authorization": http.headers["Authorization"]}, timeout=None) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            ready.set()
            for line in r.iter_lines():
                if line.startswith("event: "):
                    names.append(line[7:])
                if "dag.completed" in names or "dag.failed" in names:
                    return
    t = threading.Thread(target=listen, daemon=True)
    t.start()
    assert ready.wait(10)
    time.sleep(0.3)
    r = http.post("/api/v1/admin/run/demo/fanout", params={"scale": 0.05})
    assert r.status_code == 202, r.text
    rid = r.json()["request_id"]
    done = wait_dag(http, rid)
    t.join(10)
    assert done["status"] == "completed"
    n = {x["spec_id"]: x for x in done["nodes"]}
    assert n["join"]["started_at"] >= max(n[k]["finished_at"] for k in "abc")
    assert {"dag.started", "task.created", "task.started", "task.completed", "dag.task_finished", "dag.completed"} <= set(names)
    assert http.get("/api/v1/admin/tasks/active").json()["total"] == 0                  # nothing left running


def test_kill_a_running_node_over_http_then_the_overview_reflects_it(server, http):
    rid = http.post("/api/v1/admin/run/dag", json={"tasks": [
        {"id": "victim", "capability": "admin.test.sleep", "parameters": {"seconds": 60}},
        {"id": "ok", "capability": "admin.test.sleep", "parameters": {"seconds": 0.3}},
        {"id": "after", "capability": "admin.test.echo", "depends_on": ["victim"]}]}).json()["request_id"]
    time.sleep(0.5)
    active = http.get("/api/v1/admin/tasks/active").json()
    assert any(t["spec_id"] == "victim" and t["status"] == "running" and t["request_id"] == rid for t in active["items"])
    assert http.get("/api/v1/admin/overview").json()["active_dags"] >= 1
    assert http.post(f"/api/v1/admin/dags/{rid}/tasks/victim/kill").json()["outcome"] == "cancelled_running"
    done = wait_dag(http, rid)
    assert {n["spec_id"]: n["status"] for n in done["nodes"]} == {"victim": "cancelled", "ok": "completed", "after": "skipped"}


def test_server_shuts_down_cleanly_on_sigterm_even_with_a_task_running(server, http):
    rid = http.post("/api/v1/admin/run/dag", json={"tasks": [
        {"id": "long", "capability": "admin.test.sleep", "parameters": {"seconds": 120}, "type": "long_running"}]}).json()["request_id"]
    time.sleep(0.4)
    assert http.get(f"/api/v1/admin/dags/{rid}").json()["status"] == "running"
    proc = server["proc"]
    t0 = time.time()
    proc.send_signal(signal.SIGTERM)
    code = proc.wait(40)
    assert code == 0, f"exit code {code}\n" + server["log"].read_text()[-3000:]
    assert time.time() - t0 < 35                                                        # did not hang on the task
    log = re.sub(r"\x1b\[[0-9;]*m", "", server["log"].read_text())
    assert "Shutting down Personal AI System" in log
    # No admin background run may outlive shutdown ("Task-1" is uvicorn's own main task).
    leftovers = re.findall(r"still running: ([^\n]*)", log)
    assert not any("admin." in l for l in leftovers), leftovers
    assert "Shutdown finished with problems: 8 steps in 5.0s" not in log          # the 5 s tracker stall
