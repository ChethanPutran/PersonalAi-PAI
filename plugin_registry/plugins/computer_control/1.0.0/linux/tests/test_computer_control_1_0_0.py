"""Tests for the Linux computer_control plugin (libpai_computer_control.so).

Run from the repo root:
    .venv/bin/python -m pytest plugin_registry/plugins/computer_control/1.0.0/linux/tests -q

Layers (none of them touches your real desktop, keyboard, mouse, screen or permissions):
  * the pure logic as a C++ unit-test binary, built with ASan + UBSan
  * the library through its C ABI: manifest/contract parity, consent gates, parameter validation, apps
  * the Wayland path against a MOCK remote-desktop portal on a private D-Bus (records what would be sent)
  * a real end-to-end run: a private X server + session bus + accessibility bus with an accessible GTK app
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

import pytest

HERE = Path(__file__).resolve().parent
LINUX_DIR = HERE.parent
REGISTRY_ROOT = HERE.parents[4]
sys.path.insert(0, str(REGISTRY_ROOT / "shared" / "testing"))
from portal_harness import MockPortal, PluginSession, PrivateBus, base_env  # noqa: E402

SO = Path(os.environ.get("PAI_CC_SO", LINUX_DIR / "artifact" / "libpai_computer_control.so"))
MANIFEST = json.loads((LINUX_DIR / "manifest.json").read_text())
CAPS = {c["id"]: c for c in MANIFEST["capabilities"]}
needs_so = pytest.mark.skipif(not SO.exists(), reason=f"{SO} not built (plugin_registry/shared/build_linux.sh computer_control@1.0.0)")

CTRL, SHIFT, ALT = 0xFFE3, 0xFFE1, 0xFFE9
BTN_LEFT = 272

# docs/computer_control.md: the contract table (permissions, risk, confirmation) for every capability
CONTRACT = {
    "computer.status": ([], "low", False),
    "computer.observe": (["screen"], "medium", True),
    "computer.apps.list": ([], "low", False),
    "computer.apps.foreground": ([], "low", False),
    "computer.apps.launch": ([], "medium", True),
    "computer.click": (["input"], "high", True),
    "computer.long_press": (["input"], "high", True),
    "computer.type": (["input"], "high", True),
    "computer.key": (["input"], "high", True),
    "computer.scroll": (["input"], "high", True),
    "computer.swipe": (["input"], "high", True),
}


# ═══════════════════════════════════════════════════════════════ the pure logic

def test_unit_tests_of_the_pure_logic_pass_under_asan_and_ubsan(tmp_path):
    if not shutil.which("g++"):
        pytest.skip("g++ not available")
    out = tmp_path / "cc_unit_test"
    shared = REGISTRY_ROOT / "shared"
    build = subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Wextra", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
         f"-I{shared}", f"-I{LINUX_DIR / 'src'}", str(LINUX_DIR / "src" / "cc_model.cpp"), str(shared / "pai_desktop_apps.cpp"),
         str(shared / "pai_system_helpers.cpp"), str(HERE / "cc_unit_test.cpp"), "-o", str(out)],
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr[-2000:]
    run = subprocess.run([str(out)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert run.returncode == 0 and run.stdout.strip() == "ALL OK", run.stdout + run.stderr


# ═══════════════════════════════════════════════════════════════ ABI / manifest / contract

@needs_so
def test_library_manifest_and_contract_agree():
    out = subprocess.run([sys.executable, "-I", str(REGISTRY_ROOT / "tools" / "pai_test.py"), str(SO), "--describe"],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    assert d["name"] == "pai.computer_control" == MANIFEST["runtime"]["nativeModule"]
    assert sorted(d["capabilities"]) == sorted(CAPS) == sorted(CONTRACT)
    assert sorted(d["permissions"]) == sorted(MANIFEST["permissions"]) == ["input", "screen"]


def test_manifest_follows_the_contract_table():
    for cid, (perms, risk, confirm) in CONTRACT.items():
        c = CAPS[cid]
        assert c["permissions"] == perms, cid
        assert c["risk"] == risk, cid
        assert c["requireConfirmation"] is confirm, cid
        if confirm:
            assert c["confirmationReason"], cid
        assert c["parameters"]["type"] == "object"
    for cid in ("computer.click", "computer.type", "computer.key", "computer.swipe"):
        assert CAPS[cid]["timeoutSeconds"] >= 120, cid          # the desktop may ask the user first
    # the contract's parameters, spelled exactly
    props = lambda cid: set(CAPS[cid]["parameters"]["properties"])  # noqa: E731
    assert props("computer.observe") == {"include_tree", "include_screenshot", "max_nodes", "max_depth"}
    assert {"element_id", "x", "y", "button", "count"} <= props("computer.click")
    assert {"text", "element_id", "clear_first"} <= props("computer.type")
    assert props("computer.key") == {"keys"} and CAPS["computer.key"]["parameters"]["required"] == ["keys"]
    assert {"direction", "amount", "element_id", "x", "y"} <= props("computer.scroll")
    assert props("computer.swipe") == {"from", "to", "duration_ms"}
    assert {"element_id", "x", "y", "duration_ms"} <= props("computer.long_press")
    assert props("computer.apps.launch") == {"id", "name"} and props("computer.apps.list") == {"query"}
    assert MANIFEST["platforms"]["linux"]["artifact"] == "linux/libpai_computer_control.so"
    assert MANIFEST["deviceTypes"] == ["desktop"] and MANIFEST["architectures"] == ["x86_64"]


# ═══════════════════════════════════════════════════════════════ hermetic ABI behaviour (no desktop)

@pytest.fixture
def make(tmp_path):
    sessions: List[PluginSession] = []

    def _make(consent=True, **env) -> PluginSession:
        e = {"PAI_CC_INPUT_CONSENT": "1" if consent else "0", "PAI_CC_SCREEN_CONSENT": "1" if consent else "0",
             "PAI_CC_INPUT_BACKEND": "xtest", **env}
        s = PluginSession(SO, base_env(None, tmp_path, **e), timeout=30)
        sessions.append(s)
        return s
    yield _make
    for s in sessions:
        s.close()


@needs_so
def test_unknown_capability(make):
    assert make().call("computer.bogus")["error"] == "unknown_capability"


@needs_so
def test_nothing_acts_without_the_users_consent(make):
    s = make(consent=False)
    assert s.call("computer.observe")["error"] == "screen_not_consented"
    assert "screen permission" in s.call("computer.observe")["result"]["hint"]
    for cap, params in [("computer.click", {"x": 1, "y": 1}), ("computer.long_press", {"x": 1, "y": 1}),
                        ("computer.type", {"text": "a"}), ("computer.key", {"keys": "a"}),
                        ("computer.scroll", {"direction": "down"}), ("computer.swipe", {"from_x": 1, "from_y": 1, "to_x": 2, "to_y": 2}),
                        ("computer.apps.launch", {"name": "x"})]:
        r = s.call(cap, params)
        assert r["error"] == "input_not_consented" and r["result"]["ok"] == "false", cap
    # reading what is installed / what is in front is not control
    assert s.call("computer.apps.list")["error"] is None


@needs_so
@pytest.mark.parametrize("cap,params,error", [
    ("computer.observe", {"max_nodes": "0"}, "invalid_max_nodes"),
    ("computer.observe", {"max_depth": "99"}, "invalid_max_depth"),
    ("computer.observe", {"include_tree": "false"}, "nothing_to_observe"),
    ("computer.click", {}, "missing_target"),
    ("computer.click", {"x": "1"}, "missing_coordinates"),
    ("computer.click", {"x": "1", "y": "2", "button": "back"}, "invalid_button"),
    ("computer.click", {"x": "1", "y": "2", "count": "3"}, "invalid_count"),
    ("computer.long_press", {"x": "1", "y": "2", "duration_ms": "5"}, "invalid_duration"),
    ("computer.type", {}, "missing_required_param"),
    ("computer.type", {"text": "a\x01"}, "invalid_text"),
    ("computer.type", {"text": "a" * 501}, "text_too_long"),
    ("computer.key", {}, "missing_required_param"),
    ("computer.key", {"keys": "hyper+a"}, "unknown_modifier"),
    ("computer.key", {"keys": "ctrl+nosuch"}, "unknown_key"),
    ("computer.key", {"keys": "ctrl+"}, "invalid_keys"),
    ("computer.scroll", {}, "missing_required_param"),
    ("computer.scroll", {"direction": "sideways"}, "invalid_direction"),
    ("computer.scroll", {"direction": "up", "amount": "99"}, "invalid_amount"),
    ("computer.swipe", {}, "missing_required_param"),
    ("computer.swipe", {"from": "nope", "to": "{\"x\":1,\"y\":2}"}, "invalid_from"),
    ("computer.apps.launch", {}, "missing_required_param"),
])
def test_bad_parameters_are_refused_with_a_code_and_a_hint(make, cap, params, error):
    r = make().call(cap, params)
    assert r["error"] == error, r
    assert r["result"]["ok"] == "false" and r["result"]["error"] == error


@needs_so
def test_element_ids_need_an_observation_first(make):
    r = make().call("computer.click", {"element_id": "e1"})
    assert r["error"] == "no_observation" and "computer.observe" in r["result"]["hint"]
    assert make().call("computer.type", {"text": "a", "element_id": "e1"})["error"] == "no_observation"
    assert make().call("computer.scroll", {"direction": "up", "element_id": "e1"})["error"] == "no_observation"


@needs_so
def test_status_says_exactly_what_is_missing_and_how_to_fix_it(make):
    r = make(consent=False).call("computer.status")["result"]
    missing = json.loads(r["missing"])
    assert r["ready"] == "false" and r["platform"] == "linux"
    assert {"screen_permission", "input_permission", "accessibility_bus"} <= set(missing)
    assert "Allow the screen permission" in r["how_to_fix"] and "at-spi" in r["how_to_fix"]
    feats = json.loads(r["features"])
    assert feats["tree"] is False and feats["input"] is False and feats["ocr"] is False
    r2 = make(consent=True).call("computer.status")["result"]
    assert "screen_permission" not in json.loads(r2["missing"]) and "input_permission" not in json.loads(r2["missing"])


@needs_so
def test_observe_without_an_accessibility_bus_fails_clearly_and_still_offers_the_screenshot(make):
    r = make().call("computer.observe")
    assert r["error"] == "a11y_unavailable" and "include_screenshot" in r["result"]["hint"]
    # asking for the screenshot too still answers (the tree is what is missing)
    r2 = make().call("computer.observe", {"include_screenshot": "true"})
    assert r2["error"] is None and r2["result"]["tree"] == "null" and r2["result"]["observation_id"].startswith("obs-")
    assert "screenshot" in r2["result"]["hint"]


@pytest.fixture
def apps_dir(tmp_path):
    d = tmp_path / "share" / "applications"
    d.mkdir(parents=True)
    marker = tmp_path / "launched.txt"
    (d / "org.test.Alpha.desktop").write_text(f"[Desktop Entry]\nType=Application\nName=Alpha Editor\nExec=/bin/sh -c \"echo alpha > {marker}\" %U\n")
    (d / "org.test.Beta.desktop").write_text("[Desktop Entry]\nType=Application\nName=Beta Editor\nExec=/bin/true\n")
    (d / "org.test.Hidden.desktop").write_text("[Desktop Entry]\nType=Application\nName=Hidden Helper\nExec=/bin/true\nNoDisplay=true\n")
    (d / "org.test.Term.desktop").write_text("[Desktop Entry]\nType=Application\nName=Terminal Thing\nExec=/bin/true\nTerminal=true\n")
    (d / "org.test.Gone.desktop").write_text("[Desktop Entry]\nType=Application\nName=Gone\nExec=/bin/true\nHidden=true\n")
    return {"XDG_DATA_DIRS": str(tmp_path / "share"), "XDG_DATA_HOME": str(tmp_path / "none")}, marker


@needs_so
def test_apps_list_filters_and_hides_helpers(make, apps_dir):
    env, _ = apps_dir
    s = make(**env)
    allapps = json.loads(s.call("computer.apps.list")["result"]["apps"])
    assert [a["id"] for a in allapps] == ["org.test.Alpha", "org.test.Beta", "org.test.Term"]     # sorted; no helper, no Hidden=true
    assert allapps[0] == {"id": "org.test.Alpha", "name": "Alpha Editor"}
    assert [a["id"] for a in json.loads(s.call("computer.apps.list", {"query": "BETA"})["result"]["apps"])] == ["org.test.Beta"]
    assert json.loads(s.call("computer.apps.list", {"query": "zzz"})["result"]["apps"]) == []


@needs_so
def test_apps_launch_starts_the_app_without_a_shell_and_explains_failures(make, apps_dir):
    env, marker = apps_dir
    s = make(**env)
    r = s.call("computer.apps.launch", {"id": "org.test.Alpha"})
    assert r["error"] is None and json.loads(r["result"]["launched"]) == {"id": "org.test.Alpha", "name": "Alpha Editor"}
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.read_text().strip() == "alpha"                        # it really ran; %U was dropped
    assert s.call("computer.apps.launch", {"name": "beta editor"})["error"] is None      # by display name
    assert s.call("computer.apps.launch", {"name": "nothing-here"})["error"] == "app_not_found"
    amb = s.call("computer.apps.launch", {"name": "editor"})
    assert amb["error"] == "app_ambiguous" and "org.test.Alpha" in amb["result"]["hint"]
    assert s.call("computer.apps.launch", {"id": "org.test.Term"})["error"] == "terminal_app_not_supported"


# ═══════════════════════════════════════════════════════════════ the Wayland path (mock portal)

class Rig:
    """A private bus with a mock remote-desktop portal and one plugin process using the portal backend."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.bus = PrivateBus(tmp)
        self.portal = None
        self.plugin = None

    def up(self, plugin_env=None, **mock_env) -> "Rig":
        self.portal = MockPortal(self.bus, self.tmp, **{"MOCK_STREAMS": "0,0,1920,1080", **mock_env})
        env = {"PAI_CC_INPUT_CONSENT": "1", "PAI_CC_SCREEN_CONSENT": "1", "PAI_CC_INPUT_BACKEND": "portal",
               "PAI_CC_START_TIMEOUT": "5", **(plugin_env or {})}
        self.plugin = PluginSession(SO, base_env(self.bus, self.tmp, **env))
        return self

    def call(self, cap, **params):
        return self.plugin.call(cap, params)

    def events(self) -> List[Tuple[str, tuple]]:
        out = []
        for c in self.portal.calls():
            if c["method"].startswith("Notify") and not c.get("dead"):
                nums = c["params"].split("{}, ", 1)[1].rstrip(")").split(", ")
                out.append((c["method"][len("Notify"):], tuple(float(x) if "." in x else int(x) for x in nums)))
        return out

    def keys(self):
        return [a for m, a in self.events() if m == "KeyboardKeysym"]

    def close(self) -> None:
        if self.plugin:
            self.plugin.close()
        if self.portal:
            self.portal.close()
        self.bus.close()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


@needs_so
def test_the_first_action_asks_the_desktop_once_and_a_click_sends_motion_then_press_and_release(rig):
    rig.up()
    r = rig.call("computer.click", x=400, y=300)
    assert r["error"] is None and r["result"]["method"] == "pointer" and json.loads(r["result"]["target"]) == {"x": 400, "y": 300}
    assert [c["method"] for c in rig.portal.calls()][:4] == ["CreateSession", "SelectDevices", "SelectSources", "Start"]
    ev = rig.events()
    assert [m for m, _ in ev] == ["PointerMotionAbsolute", "PointerButton", "PointerButton"]
    assert ev[1][1][0] == BTN_LEFT and ev[1][1][1] == 1 and ev[2][1][1] == 0
    rig.call("computer.click", x=10, y=10, button="right", count=2)
    assert len(rig.portal.calls("CreateSession")) == 1                      # the approval is reused, the user is not asked again
    assert [a for m, a in rig.events() if m == "PointerButton"][-4:] == [(273, 1), (273, 0), (273, 1), (273, 0)]


@needs_so
def test_a_point_off_the_shared_screens_is_refused_never_moved_onto_one(rig):
    rig.up()
    for x, y in [(5000, 10), (-1, 10), (10, 1081)]:
        r = rig.call("computer.click", x=x, y=y)
        assert r["error"] == "point_outside_shared_screens", (x, y)
    assert [m for m, _ in rig.events() if m == "PointerButton"] == []       # nothing was pressed
    assert rig.call("computer.click", x=1919.5, y=1079.5)["error"] is None   # the last pixel is on the screen


@needs_so
def test_user_declining_the_desktops_dialog_is_reported_plainly(rig):
    rig.up(MOCK_START_CODE=1)
    r = rig.call("computer.click", x=1, y=1)
    assert r["error"] == "input_denied" and "declined" in r["result"]["hint"]
    assert rig.events() == []


@needs_so
def test_typing_sends_each_character_as_a_key_press_and_release_including_unicode(rig):
    rig.up()
    r = rig.call("computer.type", text="Hé\n€")
    assert r["error"] is None and r["result"]["method"] == "keyboard" and r["result"]["typed"] == "4"
    assert rig.keys() == [(0x48, 1), (0x48, 0), (0xE9, 1), (0xE9, 0), (0xFF0D, 1), (0xFF0D, 0), (0x010020AC, 1), (0x010020AC, 0)]


@needs_so
def test_shortcuts_hold_the_modifiers_around_the_key_and_always_release_them(rig):
    rig.up()
    assert rig.call("computer.key", keys="ctrl+shift+t")["error"] is None
    assert rig.keys() == [(CTRL, 1), (SHIFT, 1), (ord("t"), 1), (ord("t"), 0), (SHIFT, 0), (CTRL, 0)]
    assert rig.call("computer.key", keys="alt+Tab")["error"] is None
    assert rig.keys()[-4:] == [(ALT, 1), (0xFF09, 1), (0xFF09, 0), (ALT, 0)]


@needs_so
def test_scroll_long_press_and_swipe_are_balanced(rig):
    rig.up()
    assert rig.call("computer.scroll", direction="down", amount=3, x=100, y=100)["error"] is None
    assert rig.call("computer.scroll", direction="left", amount=2)["error"] is None
    axes = [a for m, a in rig.events() if m == "PointerAxisDiscrete"]
    assert axes == [(0, 3), (1, -2)]                                        # vertical +3 (down), horizontal -2 (left)
    assert rig.call("computer.long_press", x=50, y=60, duration_ms=150)["error"] is None
    assert rig.call("computer.swipe", **{"from": json.dumps({"x": 10, "y": 10}), "to": json.dumps({"x": 200, "y": 100}),
                                           "duration_ms": 100})["error"] is None
    buttons = [a for m, a in rig.events() if m == "PointerButton"]
    assert sum(1 for b in buttons if b[1] == 1) == sum(1 for b in buttons if b[1] == 0)    # every press has its release
    motions = [a for m, a in rig.events() if m == "PointerMotionAbsolute"]
    assert motions[-1][1:] == (200.0, 100.0) and len(motions) >= 6          # a path, not a jump


@needs_so
def test_revoking_consent_mid_session_stops_input_and_ends_the_desktop_session(rig):
    rig.up()
    assert rig.call("computer.click", x=5, y=5)["error"] is None
    rig.plugin.setenv(PAI_CC_INPUT_CONSENT="0")
    assert rig.call("computer.click", x=5, y=5)["error"] == "input_not_consented"
    assert len(rig.portal.calls("Session.Close")) == 1                      # the desktop's "remote control" indicator goes away


@needs_so
def test_a_runaway_agent_is_rate_limited(rig):
    rig.up(plugin_env={"PAI_CC_MAX_EVENTS_PER_MIN": "10"})
    assert rig.call("computer.type", text="abcd")["error"] is None          # 8 events
    r = rig.call("computer.type", text="abcd")
    assert r["error"] == "rate_limited"


@needs_so
def test_no_portal_installed_is_explained(tmp_path):
    bus = PrivateBus(tmp_path)
    try:
        s = PluginSession(SO, base_env(bus, tmp_path, PAI_CC_INPUT_CONSENT="1", PAI_CC_SCREEN_CONSENT="1", PAI_CC_INPUT_BACKEND="portal"))
        r = s.call("computer.click", {"x": 1, "y": 1})
        assert r["error"] == "portal_unavailable" and "xdg-desktop-portal" in r["result"]["hint"]
        s.close()
    finally:
        bus.close()


# ═══════════════════════════════════════════════════════════════ real end to end

def _gtk_available() -> bool:
    if not (shutil.which("xvfb-run") and shutil.which("dbus-run-session") and shutil.which("busctl")):
        return False
    if not (Path("/usr/libexec/at-spi-bus-launcher").exists() and Path("/usr/libexec/at-spi2-registryd").exists()):
        return False
    probe = subprocess.run(["env", "-i", "PATH=/usr/bin:/bin", "/usr/bin/python3", "-c",
                            "import gi; gi.require_version('Gtk','3.0'); gi.require_version('Atspi','2.0')"],
                           capture_output=True, text=True)
    return probe.returncode == 0


@needs_so
@pytest.mark.skipif(not _gtk_available(), reason="needs xvfb-run, dbus-run-session, at-spi2-core and python3-gi with GTK3")
def test_end_to_end_on_a_private_desktop_with_a_real_accessible_app():
    env = {**os.environ, "PAI_CC_SO": str(SO), "PAI_REGISTRY_ROOT": str(REGISTRY_ROOT)}
    for k in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "GTK_PATH", "GSETTINGS_SCHEMA_DIR"):
        env.pop(k, None)
    run = subprocess.run(
        ["dbus-run-session", "--", "xvfb-run", "-a", "-s", "-screen 0 1280x800x24", "bash", str(HERE / "e2e_session.sh"),
         sys.executable, str(HERE / "e2e_driver.py"), str(HERE / "fixtures" / "gtk_test_app.py")],
        capture_output=True, text=True, env=env, timeout=180)
    assert run.stdout.strip().endswith("E2E OK"), (run.stdout + run.stderr)[-3000:]
