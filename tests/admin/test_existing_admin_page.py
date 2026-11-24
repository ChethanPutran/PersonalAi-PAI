"""Regression guard for the admin console at the EXISTING url (GET /api/v1/system/admin).

The console was rebuilt in the HUD design as native ES modules. Every tab of the ORIGINAL single-file page
must still exist, each backed by the same endpoints, and the original page stays reachable at /admin/legacy.
Runs against a REAL uvicorn process (isolated temp DB). JS execution is covered by browser/screenshots.mjs.
"""
import re
import threading
import time
from pathlib import Path

import httpx
import pytest

from admin_helpers import start_isolated_server, stop_server

ADMIN = Path(__file__).parent.parent.parent / "src" / "pai" / "api" / "admin"
STATIC = ADMIN / "static"
LEGACY_SRC = ADMIN / "admin_legacy.html"
EMAIL, PASSWORD = "admin@example.com", "password123"
P = "/api/v1/system"

# The tabs of the ORIGINAL page (git HEAD admin.html `data-view`s) -> module that implements each one now,
# plus the endpoint strings that module must still call.
ORIGINAL_TABS = {
    "overview": ("admin_overview.js", ["adminData"]),
    "devices": ("devices.js", ["/system/devices/", "/disconnect"]),
    "plugins": ("plugins.js", []),
    "users": ("users.js", ["/system/users", "/plugins/", "/toggle"]),
    "tasks": ("alltasks.js", ["/system/tasks", "act('pause'", "act('resume'", "act('cancel'"]),
    "traces": ("traces.js", ["/system/traces", "openTraceStream"]),
    "raw": ("raw.js", ["adminData"]),
}
NEW_TABS = ["dags", "active", "playground", "events", "resources"]
# Element ids of the original page that other tooling/muscle memory relies on and that the new UI keeps.
KEPT_IDS = ["login-email", "login-password", "login-btn", "login-error", "logout-btn", "refresh-interval", "btn-refresh-now", "admin-email",
            "tasks-content", "task-filter-status", "task-filter-user", "btn-refresh-tasks", "btn-create-user",
            "trace-list", "trace-header", "trace-stats", "tree-container", "trace-detail-pane", "tree-search", "btn-expand-all",
            "btn-collapse-all", "btn-refresh-traces", "filter-user", "filter-status", "filter-errors", "filter-live",
            "splitter-aside", "splitter-detail", "raw-pre"]
ORIGINAL_LEGACY_IDS = """admin-email app btn-collapse-all btn-create-user btn-expand-all btn-refresh-now btn-refresh-tasks
btn-refresh-traces cards count-devices count-plugins count-tasks count-traces count-users devices-content filter-errors
filter-live filter-status filter-user live-dot live-text login-btn login-email login-error login-overlay login-password
logout-btn modal-backdrop modal-body plugins-content raw-pre refresh-interval splitter-aside splitter-detail
task-filter-status task-filter-user tasks-content toast-container trace-detail-pane trace-header trace-list trace-stats
tree-container tree-search users-content view-devices view-overview view-plugins view-raw view-tasks view-traces view-users""".split()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    info = start_isolated_server(tmp_path_factory.mktemp("srv_page"))
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


def test_console_is_served_at_the_original_url_with_a_strict_csp_and_no_token(server):
    r = httpx.get(server["url"] + f"{P}/admin")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.text == (STATIC / "index.html").read_text(encoding="utf-8")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "style-src 'self'" in csp and "font-src 'self'" in csp and "default-src 'none'" in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert not re.search(r"<script(?![^>]*\bsrc=)", r.text) and "<style" not in r.text and ' style="' not in r.text
    assert not re.search(r"(src|href)=\"https?://", r.text)                       # no CDN
    assert '<script type="module" src="/admin/static/js/main.js"></script>' in r.text


def test_legacy_page_is_still_reachable_and_is_the_original_file(server):
    r = httpx.get(server["url"] + f"{P}/admin/legacy")
    assert r.status_code == 200 and r.text == LEGACY_SRC.read_text(encoding="utf-8")
    for i in ORIGINAL_LEGACY_IDS:
        assert f'id="{i}"' in r.text, f"legacy page lost #{i}"
    for tab in ORIGINAL_TABS:
        assert f'data-view="{tab}"' in r.text


def test_every_original_tab_exists_in_the_new_console_and_loads_its_module(server):
    main = (STATIC / "js" / "main.js").read_text()
    routes = re.findall(r"\{ id: '(\w+)', label: '([^']+)'.*?mod: '(\w+)'", main)
    ids = {r[0] for r in routes}
    assert set(ORIGINAL_TABS) | set(NEW_TABS) <= ids, f"missing nav entries: {(set(ORIGINAL_TABS) | set(NEW_TABS)) - ids}"
    mods = {r[0]: r[2] for r in routes}
    with httpx.Client(base_url=server["url"]) as anon:
        for tab, (module, needles) in ORIGINAL_TABS.items():
            assert mods[tab] + ".js" == module or mods[tab] == module.removesuffix(".js"), (tab, mods[tab])
            src = (STATIC / "js" / "views" / module).read_text()
            for n in needles:
                assert n in src, f"{module} no longer references {n}"
            assert anon.get(f"/admin/static/js/views/{module}").status_code == 200
        for tab in NEW_TABS:
            assert anon.get(f"/admin/static/js/views/{mods[tab]}.js").status_code == 200
    # the shared aggregate poller keeps the legacy endpoint, interval key and token key
    assert "/system/admin/data" in (STATIC / "js" / "admindata.js").read_text()
    assert "pai_admin_refresh_ms" in (STATIC / "js" / "admindata.js").read_text()
    api_js = (STATIC / "js" / "api.js").read_text()
    assert "pai_admin_token" in api_js and "/auth/login" in api_js and "/auth/me" in api_js and "/system/traces/stream" in api_js


def test_kept_element_ids_are_present_in_the_modules():
    text = "\n".join(p.read_text() for p in (STATIC / "js").rglob("*.js"))
    for i in KEPT_IDS:
        assert f"'{i}'" in text or f'"{i}"' in text or f"id = '{i}'" in text, f"#{i} disappeared from the UI code"


def test_traces_view_keeps_its_features():
    src = (STATIC / "js" / "views" / "traces.js").read_text()
    tree = (STATIC / "js" / "trace_tree.js").read_text()
    for feature in ["pai_admin_traces_aside_w", "pai_admin_traces_detail_w", "Token usage", "Parameters (input)", "Result (output)", "incomplete",
                    "has_error", "user_id", "Expand all", "Collapse all", "scheduleTraceRefresh", "role: 'separator'"]:
        assert feature in src, feature
    for kind in ["llm.request", "llm.response", "tool.call", "tool.result", "span.start", "trace.start", "error", "log"]:
        assert kind in tree, kind


# ------------------------------------------------------------ each original view's backing endpoints
def test_overview_devices_plugins_users_raw_all_read_admin_data(http):
    r = http.get(f"{P}/admin/data")
    assert r.status_code == 200
    d = r.json()
    for key in ("summary", "devices", "plugins"):
        assert key in d, key
    assert isinstance(d["devices"], list) and isinstance(d["plugins"], list)


def test_users_view_endpoints(http):
    r = http.get(f"{P}/users")
    assert r.status_code == 200
    users = r.json()
    users = users.get("users", users) if isinstance(users, dict) else users
    assert any(u["email"] == EMAIL for u in users)
    # CRUD: create -> patch -> delete, exactly what the Users tab's buttons call
    c = http.post(f"{P}/users", json={"email": "crud@example.com", "password": "password123", "is_admin": False})
    assert c.status_code == 201, c.text
    uid = c.json().get("id") or c.json().get("user_id") or c.json()["user"]["id"]
    assert http.patch(f"{P}/users/{uid}", json={"is_active": False}).status_code == 200
    assert http.delete(f"{P}/users/{uid}").status_code == 204


def test_devices_and_plugins_view_endpoints(http):
    assert http.get(f"{P}/devices").status_code == 200
    assert http.get(f"{P}/plugins").status_code == 200


def test_tasks_view_endpoints(http):
    r = http.get(f"{P}/tasks", params={"limit": 10})
    assert r.status_code == 200
    body = r.json()
    items = body.get("tasks", body.get("items", body)) if isinstance(body, dict) else body
    assert isinstance(items, list)
    assert http.get(f"{P}/tasks", params={"status": "running"}).status_code == 200
    # detail view (row click): needs a real task -> run one through the new admin API first
    rid = http.post("/api/v1/admin/run/demo/chain", params={"scale": 0.05}).json()["request_id"]
    time.sleep(1.0)
    tid = http.get(f"/api/v1/admin/dags/{rid}").json()["nodes"][0]["runtime_task_id"]
    d = http.get(f"{P}/tasks/{tid}")
    assert d.status_code == 200 and d.json()["id"] == tid
    # NOTE: GET {P}/tasks/<unknown> currently answers 500 (pre-existing in system.py, left untouched).


def test_traces_view_list_detail_and_live_stream(server, http):
    # produce at least one trace: a DAG run goes through the orchestrator's tracing
    rid = http.post("/api/v1/admin/run/demo/chain", params={"scale": 0.05}).json()["request_id"]
    end = time.time() + 20
    while time.time() < end and http.get(f"/api/v1/admin/dags/{rid}").json()["status"] == "running":
        time.sleep(0.1)
    r = http.get(f"{P}/traces", params={"limit": 20})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, (list, dict))
    traces = body if isinstance(body, list) else next((v for v in body.values() if isinstance(v, list)), [])
    if traces:                                                                   # detail fetch, as the list click does
        tid = traces[0].get("trace_id") or traces[0].get("id")
        d = http.get(f"{P}/traces/{tid}")
        assert d.status_code == 200, d.text
    assert http.get(f"{P}/traces/definitely-not-a-trace").status_code in (404, 200)
    # live stream connects and is text/event-stream
    seen = threading.Event()
    info = {}

    def listen():
        with httpx.stream("GET", server["url"] + f"{P}/traces/stream", headers={"Authorization": http.headers["Authorization"]}, timeout=None) as s:
            info["status"], info["ctype"] = s.status_code, s.headers.get("content-type", "")
            seen.set()
    threading.Thread(target=listen, daemon=True).start()
    assert seen.wait(10)
    assert info["status"] == 200 and info["ctype"].startswith("text/event-stream")


def test_login_and_me_endpoints_used_by_the_page(http):
    me = http.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["is_admin"] is True
    assert httpx.post(str(http.base_url) + "/api/v1/auth/login", json={"email": EMAIL, "password": "wrong-password"}).status_code in (400, 401)


def test_every_original_endpoint_still_requires_an_admin_token(server):
    with httpx.Client(base_url=server["url"]) as anon:
        for path in ("/admin/data", "/tasks", "/users", "/devices", "/plugins", "/traces", "/traces/stream"):
            assert anon.get(P + path).status_code == 401, path


def test_new_admin_api_is_not_duplicating_system_task_listing(http):
    paths = http.get("/openapi.json").json()["paths"]
    assert "/api/v1/admin/tasks" not in paths and "/api/v1/admin/tasks/{task_id}" not in paths
    assert "/api/v1/admin/tasks/active" in paths and f"{P}/tasks" in paths
