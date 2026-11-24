"""Hermetic tests for the Linux input plugin (libpai_input.so).

Run from the repo root:
    .venv/bin/python -m pytest plugin_registry/plugins/input/1.0.0/linux/tests -q

The real library is loaded through the C ABI and talks to a MOCK remote-desktop portal on a private
D-Bus (plugin_registry/shared/testing). Nothing here touches your real keyboard, mouse, screen, or
desktop permissions: the mock only RECORDS the events the plugin would have sent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

HERE = Path(__file__).resolve().parent
LINUX_DIR = HERE.parent
REGISTRY_ROOT = HERE.parents[4]
sys.path.insert(0, str(REGISTRY_ROOT / "shared" / "testing"))
from portal_harness import MockPortal, PluginSession, PrivateBus, base_env  # noqa: E402

SO = Path(os.environ.get("PAI_INPUT_SO", LINUX_DIR / "artifact" / "libpai_input.so"))
MANIFEST = json.loads((LINUX_DIR / "manifest.json").read_text())
pytestmark = pytest.mark.skipif(not SO.exists(), reason=f"{SO} not built")

KEYSYM_RETURN, KEYSYM_TAB = 0xFF0D, 0xFF09
CTRL, SHIFT, ALT, SUPER = 0xFFE3, 0xFFE1, 0xFFE9, 0xFFEB
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 272, 273, 274
ACTIONS = ["keyboard.type", "keyboard.key", "mouse.move", "mouse.click", "mouse.scroll"]
VALID = {"keyboard.type": {"text": "a"}, "keyboard.key": {"keys": "a"}, "mouse.move": {"x": 1, "y": 1},
         "mouse.click": {"x": 1, "y": 1}, "mouse.scroll": {"dy": 1}}


class Rig:
    """A private desktop with a mock portal and one plugin process."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.bus = PrivateBus(tmp)
        self.portal = None
        self.plugin = None

    def up(self, portal: bool = True, plugin_env: dict | None = None, **mock_env) -> "Rig":
        if portal:
            self.portal = MockPortal(self.bus, self.tmp, **mock_env)
        env = {"PAI_INPUT_CONSENT": "1", "PAI_INPUT_START_TIMEOUT": "5", **(plugin_env or {})}
        self.plugin = PluginSession(SO, base_env(self.bus, self.tmp, **env))
        return self

    def ready(self, **mock_env) -> "Rig":
        """Portal up AND the user's approval granted (input.start succeeded)."""
        self.up(**mock_env)
        r = self.plugin.call("input.start")
        assert r["error"] is None, r
        return self

    def call(self, capability: str, **params):
        return self.plugin.call(capability, params)

    def events(self, attempts: bool = False) -> List[Tuple[str, tuple]]:
        """Events the portal accepted; with attempts=True also those sent to an already-dead session."""
        out = []
        for c in self.portal.calls():
            if c["method"].startswith("Notify") and (attempts or not c.get("dead")):
                nums = c["params"].split("{}, ", 1)[1].rstrip(")").split(", ")
                out.append((c["method"][len("Notify"):], tuple(float(x) if "." in x else int(x) for x in nums)))
        return out

    def keys(self) -> List[Tuple[int, int]]:
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


def presses(keys):
    """[(keysym, 1), (keysym, 0), ...] -> the keysyms that were pressed, in order."""
    return [k for k, s in keys if s == 1]


# ── registration / manifest ─────────────────────────────────────────────────

def test_library_and_manifest_agree():
    out = subprocess.run([sys.executable, "-I", str(REGISTRY_ROOT / "tools" / "pai_test.py"), str(SO), "--describe"],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    assert d["name"] == "pai.input" == MANIFEST["runtime"]["nativeModule"]
    assert sorted(d["capabilities"]) == sorted(c["id"] for c in MANIFEST["capabilities"]) and len(d["capabilities"]) == 8
    assert d["permissions"] == ["input"] == MANIFEST["permissions"]


def test_every_action_needs_confirmation_and_the_risky_ones_are_high():
    caps = {c["id"]: c for c in MANIFEST["capabilities"]}
    for cid in ("input.start", *ACTIONS):
        assert caps[cid]["requireConfirmation"] is True, cid
        assert caps[cid]["confirmationReason"], cid
    for cid in ("input.start", "keyboard.type", "keyboard.key", "mouse.click", "mouse.move", "mouse.scroll"):
        assert caps[cid]["risk"] == "high", cid           # the registry classes input control as high; stay honest
    for cid in ("input.status", "input.stop"):
        assert caps[cid]["requireConfirmation"] is False                 # reading status / releasing control is always allowed
    assert caps["input.start"]["timeoutSeconds"] >= 120                  # a human reads the dialog


def test_unknown_capability(rig):
    assert rig.up().call("input.bogus")["error"] == "unknown_capability"


# ── gates ───────────────────────────────────────────────────────────────────

def test_nothing_works_without_the_apps_consent(rig):
    rig.up(plugin_env={"PAI_INPUT_CONSENT": "0"})
    for cap in ("input.start", *ACTIONS):
        assert rig.call(cap, **VALID.get(cap, {}))["error"] == "input_not_consented", cap
    assert rig.portal.calls() == []                                       # the desktop was never even asked
    assert rig.call("input.status")["result"]["active"] == "false"       # status/stop stay available
    assert rig.call("input.stop")["error"] is None


@pytest.mark.parametrize("cap", ACTIONS)
def test_actions_need_input_start_first(rig, cap):
    rig.up()
    r = rig.call(cap, **VALID[cap])
    assert r["error"] == "input_not_started" and "input.start" in r["result"]["hint"]
    assert rig.portal.calls() == []


# ── input.start: the desktop's approval ─────────────────────────────────────

def test_start_runs_the_portal_handshake_and_reports_what_was_granted(rig):
    rig.up(MOCK_STREAMS="0,0,1920,1080;1920,0,1366,768")
    r = rig.call("input.start")
    assert r["error"] is None and r["result"]["active"] == "true" and r["result"]["already_active"] == "false"
    assert json.loads(r["result"]["screens"]) == [{"x": 0, "y": 0, "w": 1920, "h": 1080}, {"x": 1920, "y": 0, "w": 1366, "h": 768}]
    assert r["result"]["desktop"] == "0,0,3286,1080"
    assert "several monitors" in r["result"]["hint"]

    calls = rig.portal.calls()
    assert [c["method"] for c in calls] == ["CreateSession", "SelectDevices", "SelectSources", "Start"]
    assert "'types': <uint32 3>" in calls[1]["params"]                    # keyboard + pointer only (no touch)
    assert "'types': <uint32 1>" in calls[2]["params"] and "'multiple': <true>" in calls[2]["params"]
    tokens = [c["params"].split("'handle_token': <'")[1].split("'")[0] for c in calls]
    assert len(set(tokens)) == 4                                         # every request has its own token


def test_second_start_reuses_the_approval(rig):
    rig.ready()
    r = rig.call("input.start")
    assert r["result"]["already_active"] == "true"
    assert len(rig.portal.calls("CreateSession")) == 1


def test_session_handle_given_as_an_object_path_is_accepted(rig):
    rig.up(MOCK_SESSION_AS_PATH=1)
    assert rig.call("input.start")["error"] is None


def test_a_slow_user_or_portal_is_fine(rig):
    rig.up(MOCK_DELAY_MS=400)
    assert rig.call("input.start")["error"] is None


def test_user_declining_ends_the_attempt_and_releases_the_session(rig):
    rig.up(MOCK_START_CODE=1)
    r = rig.call("input.start")
    assert r["error"] == "input_denied" and "declined" in r["result"]["hint"]
    assert len(rig.portal.calls("Session.Close")) == 1                    # no dangling half-open session
    assert rig.call("input.status")["result"]["active"] == "false"
    assert rig.call("keyboard.type", text="x")["error"] == "input_not_started"


def test_no_answer_to_the_dialog_times_out_and_cleans_up(rig):
    rig.up(MOCK_NO_RESPONSE="Start", plugin_env={"PAI_INPUT_START_TIMEOUT": "1"})
    r = rig.call("input.start")
    assert r["error"] == "input_timeout"
    assert len(rig.portal.calls("Session.Close")) == 1


def test_portal_failure_codes(rig):
    rig.up(MOCK_START_CODE=2)
    assert rig.call("input.start")["error"] == "input_failed"


def test_no_portal_installed(rig):
    rig.up(portal=False)
    r = rig.call("input.start")
    assert r["error"] == "portal_unavailable" and "xdg-desktop-portal" in r["result"]["hint"]


def test_devices_the_user_did_not_allow_stay_off_limits(rig):
    rig.ready(MOCK_DEVICES=1)                                             # keyboard only
    assert rig.call("keyboard.type", text="a", delayMs=0)["error"] is None
    assert rig.call("mouse.click", x=5, y=5)["error"] == "pointer_not_granted"
    assert rig.call("mouse.scroll", dy=1)["error"] == "pointer_not_granted"
    assert [m for m, _ in rig.events()] == ["KeyboardKeysym", "KeyboardKeysym"]


def test_pointer_only_grant(rig):
    rig.ready(MOCK_DEVICES=2)
    assert rig.call("keyboard.type", text="a")["error"] == "keyboard_not_granted"
    assert rig.call("keyboard.key", keys="Return")["error"] == "keyboard_not_granted"
    assert rig.call("mouse.click", x=5, y=5)["error"] is None


def test_no_screen_shared_means_no_absolute_positioning(rig):
    rig.up(MOCK_STREAMS="")
    r = rig.call("input.start")
    assert r["error"] is None and r["result"]["screens"] == "[]" and "no screen was shared" in r["result"]["hint"]
    assert rig.call("mouse.move", x=5, y=5)["error"] == "no_screens_shared"
    assert rig.call("keyboard.type", text="ok", delayMs=0)["error"] is None   # the keyboard needs no screen


def test_stop_releases_control_and_start_can_begin_again(rig):
    rig.ready()
    assert rig.call("input.stop")["result"]["active"] == "false"
    assert len(rig.portal.calls("Session.Close")) == 1
    assert rig.call("keyboard.type", text="x")["error"] == "input_not_started"
    assert rig.call("input.start")["error"] is None
    assert len(rig.portal.calls("CreateSession")) == 2


# ── keyboard.type ───────────────────────────────────────────────────────────

def test_type_sends_press_and_release_for_every_character(rig):
    rig.ready()
    r = rig.call("keyboard.type", text="Hi!", delayMs=0)
    assert r["result"] == {"ok": "true", "typed": "3"}
    assert rig.keys() == [(72, 1), (72, 0), (105, 1), (105, 0), (33, 1), (33, 0)]


def test_type_handles_newline_tab_and_unicode(rig):
    rig.ready()
    rig.call("keyboard.type", text="a\n\tz é€😀", delayMs=0)
    assert presses(rig.keys()) == [97, KEYSYM_RETURN, KEYSYM_TAB, 122, 32, 0xE9, 0x01000000 | 0x20AC, 0x01000000 | 0x1F600]


@pytest.mark.parametrize("params,err", [
    ({}, "missing_required_param"), ({"text": ""}, "missing_required_param"),
    ({"text": "x" * 501}, "text_too_long"), ({"text": "a\x07b"}, "invalid_text"), ({"text": "a\x1bb"}, "invalid_text"),
    ({"text": "ok", "delayMs": 500}, "invalid_delay"), ({"text": "ok", "delayMs": -1}, "invalid_delay"),
    ({"text": "ok", "delayMs": "fast"}, "invalid_delay"),
])
def test_type_validates_before_sending_anything(rig, params, err):
    rig.ready()
    assert rig.call("keyboard.type", **params)["error"] == err
    assert rig.events() == []                                             # no partial typing on a bad request


def test_type_accepts_exactly_the_maximum_length(rig):
    rig.ready()
    r = rig.call("keyboard.type", text="x" * 500, delayMs=0)
    assert r["result"]["typed"] == "500" and len(rig.keys()) == 1000


# ── keyboard.key ────────────────────────────────────────────────────────────

def test_single_keys(rig):
    rig.ready()
    for spec, sym in (("Return", KEYSYM_RETURN), ("enter", KEYSYM_RETURN), ("ESCAPE", 0xFF1B), ("Tab", KEYSYM_TAB),
                      ("Left", 0xFF51), ("F5", 0xFFC2), ("f12", 0xFFC9), ("a", 97), ("5", 53), ("plus", 43), ("space", 32)):
        before = len(rig.keys())
        assert rig.call("keyboard.key", keys=spec)["error"] is None, spec
        assert rig.keys()[before:] == [(sym, 1), (sym, 0)], spec


def test_shortcuts_press_modifiers_first_and_release_them_in_reverse(rig):
    rig.ready()
    rig.call("keyboard.key", keys="ctrl+shift+t")
    assert rig.keys() == [(CTRL, 1), (SHIFT, 1), (116, 1), (116, 0), (SHIFT, 0), (CTRL, 0)]
    before = len(rig.keys())
    rig.call("keyboard.key", keys="Alt+Left")
    assert rig.keys()[before:] == [(ALT, 1), (0xFF51, 1), (0xFF51, 0), (ALT, 0)]


def test_a_bare_modifier_can_be_pressed(rig):
    rig.ready()
    rig.call("keyboard.key", keys="super")
    assert rig.keys() == [(SUPER, 1), (SUPER, 0)]


def test_key_repeat(rig):
    rig.ready()
    assert rig.call("keyboard.key", keys="Down", count=3)["result"]["count"] == "3"
    assert presses(rig.keys()) == [0xFF54] * 3


@pytest.mark.parametrize("params,err", [
    ({}, "missing_required_param"), ({"keys": ""}, "missing_required_param"),
    ({"keys": "ctrl+"}, "invalid_keys"), ({"keys": "+a"}, "invalid_keys"), ({"keys": "ctrl++a"}, "invalid_keys"),
    ({"keys": "hyper+a"}, "unknown_modifier"), ({"keys": "ctrl+nosuchkey"}, "unknown_key"), ({"keys": "ab"}, "unknown_key"),
    ({"keys": "a", "count": 0}, "invalid_count"), ({"keys": "a", "count": 51}, "invalid_count"),
])
def test_key_validation_sends_nothing(rig, params, err):
    rig.ready()
    assert rig.call("keyboard.key", **params)["error"] == err
    assert rig.events() == []


# ── mouse.move / click / scroll ─────────────────────────────────────────────

def test_move_in_desktop_pixels_on_one_screen(rig):
    rig.ready()
    r = rig.call("mouse.move", x=960, y=540)
    assert r["result"]["screen_x"] == "960" and rig.events() == [("PointerMotionAbsolute", (40, 960.0, 540.0))]


def test_move_in_screenshot_space_is_scaled_onto_the_desktop(rig):
    rig.ready()                                                           # 1920x1080 shared
    rig.call("mouse.move", x=100, y=50, spaceWidth=200, spaceHeight=100)  # the middle of a 200x100 screenshot
    assert rig.events() == [("PointerMotionAbsolute", (40, 960.0, 540.0))]
    rig.call("mouse.move", x=3840, y=2160, spaceWidth=3840, spaceHeight=2160)   # a HiDPI screenshot's last pixel
    assert rig.events()[-1] == ("PointerMotionAbsolute", (40, 1919.5, 1079.5))


def test_two_monitors_pick_the_right_screen_and_local_coordinates(rig):
    rig.ready(MOCK_STREAMS="0,0,1920,1080;1920,0,1366,768")
    rig.call("mouse.move", x=2000, y=100)                                 # on the second monitor
    rig.call("mouse.move", x=1919, y=1079)                                # bottom-right of the first
    rig.call("mouse.move", x=2000, y=100, spaceWidth=3286, spaceHeight=1080)     # same point, screenshot space
    assert rig.events() == [("PointerMotionAbsolute", (41, 80.0, 100.0)),
                            ("PointerMotionAbsolute", (40, 1919.0, 1079.0)),
                            ("PointerMotionAbsolute", (41, 80.0, 100.0))]


def test_monitor_left_of_the_origin(rig):
    rig.ready(MOCK_STREAMS="-1920,0,1920,1080;0,0,2560,1440")
    rig.call("mouse.move", x=0, y=0, spaceWidth=4480, spaceHeight=1440)   # the screenshot's top-left = global (-1920, 0)
    rig.call("mouse.move", x=-100, y=200)                                 # desktop pixels, left monitor
    assert rig.events() == [("PointerMotionAbsolute", (40, 0.0, 0.0)), ("PointerMotionAbsolute", (40, 1820.0, 200.0))]


def test_a_point_on_a_screen_the_user_did_not_share_is_refused(rig):
    rig.ready(MOCK_STREAMS="0,0,1920,1080;1920,0,1366,768")               # the second monitor is shorter
    r = rig.call("mouse.move", x=2000, y=900)                             # below it: not on any shared screen
    assert r["error"] == "point_outside_shared_screens" and rig.events() == []


def test_only_the_exact_edge_is_clamped_anything_beyond_is_refused(rig):
    rig.ready()                                                           # 1920x1080
    assert rig.call("mouse.move", x=1920, y=1080)["error"] is None        # the exact far edge = the last pixel
    assert rig.events() == [("PointerMotionAbsolute", (40, 1919.5, 1079.5))]
    for x, y in ((1921, 10), (10, 1081), (5000, 5000), (-1, 10), (10, -1)):
        assert rig.call("mouse.move", x=x, y=y)["error"] == "point_outside_shared_screens", (x, y)
    assert len(rig.events()) == 1                                         # nothing was moved for the refused ones


def test_the_unshared_monitor_case(rig):
    rig.ready(MOCK_STREAMS="0,0,1920,1080")                               # user shared only the first monitor
    assert rig.call("mouse.click", x=2500, y=100)["error"] == "point_outside_shared_screens"
    assert rig.events() == []


@pytest.mark.parametrize("params,err", [
    ({}, "missing_coordinates"), ({"x": 5}, "missing_coordinates"), ({"x": "abc", "y": 5}, "missing_coordinates"),
    ({"x": "nan", "y": 5}, "missing_coordinates"), ({"x": "inf", "y": 5}, "missing_coordinates"),
    ({"x": 5, "y": 5, "spaceWidth": 100}, "invalid_space"), ({"x": 5, "y": 5, "spaceHeight": 100}, "invalid_space"),
    ({"x": 5, "y": 5, "spaceWidth": 0, "spaceHeight": 10}, "invalid_space"),
    ({"x": 5, "y": 5, "spaceWidth": -5, "spaceHeight": 10}, "invalid_space"),
    ({"x": 5, "y": 5, "spaceWidth": 1e9, "spaceHeight": 10}, "invalid_space"),
    ({"x": -1, "y": 5, "spaceWidth": 100, "spaceHeight": 100}, "point_outside_screen"),
    ({"x": 101, "y": 5, "spaceWidth": 100, "spaceHeight": 100}, "point_outside_screen"),
    ({"x": 5, "y": 101, "spaceWidth": 100, "spaceHeight": 100}, "point_outside_screen"),
])
def test_coordinate_validation(rig, params, err):
    rig.ready()
    assert rig.call("mouse.move", **params)["error"] == err
    assert rig.events() == []


def test_click_moves_then_presses_and_releases(rig):
    rig.ready()
    r = rig.call("mouse.click", x=100, y=200)
    assert r["result"] == {"ok": "true", "button": "left", "count": "1", "moved": "true"}
    assert rig.events() == [("PointerMotionAbsolute", (40, 100.0, 200.0)), ("PointerButton", (BTN_LEFT, 1)), ("PointerButton", (BTN_LEFT, 0))]


def test_click_without_a_position_clicks_where_the_pointer_is(rig):
    rig.ready()
    assert rig.call("mouse.click")["result"]["moved"] == "false"
    assert rig.events() == [("PointerButton", (BTN_LEFT, 1)), ("PointerButton", (BTN_LEFT, 0))]


def test_buttons_and_double_click(rig):
    rig.ready()
    rig.call("mouse.click", button="right")
    rig.call("mouse.click", button="MIDDLE")
    rig.call("mouse.click", count=2)
    assert [a for m, a in rig.events() if m == "PointerButton"] == [
        (BTN_RIGHT, 1), (BTN_RIGHT, 0), (BTN_MIDDLE, 1), (BTN_MIDDLE, 0), (BTN_LEFT, 1), (BTN_LEFT, 0), (BTN_LEFT, 1), (BTN_LEFT, 0)]


@pytest.mark.parametrize("params,err", [
    ({"button": "back"}, "invalid_button"), ({"count": 0}, "invalid_count"), ({"count": 4}, "invalid_count"),
    ({"count": "two"}, "invalid_count"), ({"x": 5}, "missing_coordinates"),
])
def test_click_validation_presses_nothing(rig, params, err):
    rig.ready()
    assert rig.call("mouse.click", **params)["error"] == err
    assert rig.events() == []


def test_scroll(rig):
    rig.ready()
    rig.call("mouse.scroll", dy=3)
    rig.call("mouse.scroll", dy=-2)
    rig.call("mouse.scroll", dx=1)
    rig.call("mouse.scroll", dx=-4, dy=5)
    assert rig.events() == [("PointerAxisDiscrete", (0, 3)), ("PointerAxisDiscrete", (0, -2)), ("PointerAxisDiscrete", (1, 1)),
                            ("PointerAxisDiscrete", (0, 5)), ("PointerAxisDiscrete", (1, -4))]


def test_scroll_at_a_position_moves_there_first(rig):
    rig.ready()
    rig.call("mouse.scroll", dy=2, x=300, y=400)
    assert rig.events() == [("PointerMotionAbsolute", (40, 300.0, 400.0)), ("PointerAxisDiscrete", (0, 2))]


@pytest.mark.parametrize("params,err", [
    ({}, "missing_required_param"), ({"dy": 0, "dx": 0}, "missing_required_param"), ({"dy": 51}, "invalid_scroll"),
    ({"dx": -51}, "invalid_scroll"), ({"dy": "lots"}, "invalid_scroll"), ({"dy": 1, "x": 5}, "missing_coordinates"),
])
def test_scroll_validation(rig, params, err):
    rig.ready()
    assert rig.call("mouse.scroll", **params)["error"] == err
    assert rig.events() == []


# ── rate limit and a revoked session ────────────────────────────────────────

def test_event_rate_limit_stops_a_runaway(rig):
    rig.up(plugin_env={"PAI_INPUT_MAX_EVENTS_PER_MIN": "10"})
    assert rig.call("input.start")["error"] is None
    assert rig.call("keyboard.type", text="abcdefg", delayMs=0)["error"] == "rate_limited"     # 14 events > 10
    assert rig.events() == []                                                                    # refused as a whole
    assert rig.call("keyboard.type", text="abc", delayMs=0)["error"] is None                    # 6 events
    assert rig.call("keyboard.type", text="abc", delayMs=0)["error"] == "rate_limited"          # 12 > 10
    assert len(rig.keys()) == 6


def test_a_session_revoked_by_the_user_is_noticed(rig):
    rig.ready(MOCK_DIE_AFTER_NOTIFY=2)                                     # the desktop drops the session mid-typing
    r = rig.call("keyboard.type", text="abcd", delayMs=0)
    assert r["error"] == "session_closed" and r["result"]["typed"] == "1"
    assert rig.call("input.status")["result"]["active"] == "false"
    assert rig.call("mouse.click", x=1, y=1)["error"] == "input_not_started"
    assert rig.call("input.start")["error"] is None                         # and control can be re-requested


def test_a_transient_failure_mid_chord_still_releases_the_modifiers_it_pressed(rig):
    rig.ready(MOCK_FAIL_NOTIFY_AT=3)                                       # ctrl down, shift down, then 't' fails once
    r = rig.call("keyboard.key", keys="ctrl+shift+t")
    assert r["error"] == "notify_failed"
    # The plugin must STILL let go of shift and ctrl (reverse order): no stuck modifier on a live session.
    assert [a for m, a in rig.events() if m == "KeyboardKeysym"] == [(CTRL, 1), (SHIFT, 1), (SHIFT, 0), (CTRL, 0)]
    assert rig.call("input.status")["result"]["active"] == "true"          # a transient error does not end the session


def test_a_dead_session_is_closed_not_leaked(rig):
    rig.ready(MOCK_DIE_AFTER_NOTIFY=2)                                     # ctrl down, shift down, then the session dies
    r = rig.call("keyboard.key", keys="ctrl+shift+t")
    assert r["error"] == "session_closed"
    assert rig.keys() == [(CTRL, 1), (SHIFT, 1)]
    # Closing the session is what releases anything still held and clears the desktop's indicator.
    assert len(rig.portal.calls("Session.Close")) == 1
    assert rig.call("input.status")["result"]["active"] == "false"
    assert rig.call("mouse.click", x=1, y=1)["error"] == "input_not_started"


def test_revoking_consent_ends_the_open_desktop_session(rig):
    rig.ready()
    rig.plugin.setenv(PAI_INPUT_CONSENT=None)                 # the app clears it the moment the user revokes
    r = rig.call("keyboard.type", text="x", delayMs=0)
    assert r["error"] == "input_not_consented"
    assert len(rig.portal.calls("Session.Close")) == 1        # the desktop's "remote control" session is released
    assert rig.events() == []
    assert rig.call("input.status")["result"]["active"] == "false"
    rig.plugin.setenv(PAI_INPUT_CONSENT=1)                    # consent given again: still has to start over
    assert rig.call("mouse.click", x=1, y=1)["error"] == "input_not_started"
