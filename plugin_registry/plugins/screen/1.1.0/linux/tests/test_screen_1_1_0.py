"""Hermetic tests for the Linux screen plugin (libpai_screen.so).

Run from the repo root:
    .venv/bin/python -m pytest plugin_registry/plugins/screen/1.1.0/linux/tests -q

The real library is loaded through the C ABI and talks to a MOCK desktop portal on a private D-Bus
(plugin_registry/shared/testing): the real portal, your screen and your permission store are never
touched. Command-line capture tools are stubs on a private PATH.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LINUX_DIR = HERE.parent
sys.path.insert(0, str(HERE.parents[4] / "shared" / "testing"))
from portal_harness import MockPortal, PluginSession, PrivateBus, base_env, tiny_png  # noqa: E402

SO = Path(os.environ.get("PAI_SCREEN_SO", LINUX_DIR / "artifact" / "libpai_screen.so"))
MANIFEST = json.loads((LINUX_DIR / "manifest.json").read_text())
pytestmark = pytest.mark.skipif(not SO.exists(), reason=f"{SO} not built")


class Desk:
    """A private desktop: bus + (optional) mock portal + stub tools + plugin sessions."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.bus = PrivateBus(tmp)
        self.portal = None
        self.sessions = []
        self.out = tmp / "out"
        self.out.mkdir()

    def portal_up(self, **env) -> MockPortal:
        self.shot = self.tmp / "portal_made_this.png"            # what the (mock) portal says it captured
        self.shot.write_bytes(tiny_png(3, 2))
        self.portal = MockPortal(self.bus, self.tmp, MOCK_SCREENSHOT_URI=self.shot.as_uri(), **env)
        return self.portal

    def stub_tool(self, name: str, body: str) -> None:
        f = self.tmp / "bin" / name
        (self.tmp / "bin").mkdir(exist_ok=True)
        f.write_text("#!/bin/sh\n" + body + "\n")
        f.chmod(0o755)

    def plugin(self, **env) -> PluginSession:
        settings = {"PAI_SCREEN_CONSENT": "1", "PAI_SCREEN_OUTPUT_ROOTS": str(self.tmp), **env}   # tests may override
        e = base_env(self.bus, self.tmp, **settings)
        s = PluginSession(SO, e)
        self.sessions.append(s)
        return s

    def close(self) -> None:
        for s in self.sessions:
            s.close()
        if self.portal:
            self.portal.close()
        self.bus.close()


@pytest.fixture
def desk(tmp_path):
    d = Desk(tmp_path)
    yield d
    d.close()


def out(desk: Desk, name: str = "shot.png") -> str:
    return str(desk.out / name)


# ── registration / manifest ─────────────────────────────────────────────────

def test_library_and_manifest_agree(desk):
    assert MANIFEST["version"] == "1.1.0" and MANIFEST["runtime"]["nativeModule"] == "pai.screen"
    assert [c["id"] for c in MANIFEST["capabilities"]] == ["screen.capture"]
    assert MANIFEST["platforms"]["linux"]["artifact"] == "linux/libpai_screen.so"
    cap = MANIFEST["capabilities"][0]
    assert cap["requireConfirmation"] is True and cap["risk"] == "high" and cap["timeoutSeconds"] >= 60


def test_unknown_capability(desk):
    assert desk.plugin().call("screen.nothing")["error"] == "unknown_capability"


# ── the portal path (the fix: GNOME has no wlr-screencopy) ──────────────────

def test_portal_capture_returns_a_verified_private_png(desk):
    portal = desk.portal_up()
    r = desk.plugin().call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] is None and r["result"]["ok"] == "true" and r["result"]["tool"] == "portal"
    assert (r["result"]["width"], r["result"]["height"]) == ("3", "2")
    f = Path(r["result"]["path"])
    assert f.read_bytes() == tiny_png(3, 2)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600                      # a screenshot is owner-only
    assert not desk.shot.exists()                                        # the portal's own copy is cleaned up
    call = portal.calls("Screenshot")[0]["params"]
    assert "'interactive': <false>" in call and "handle_token" in call   # silent capture, predictable request path


def test_response_arriving_after_the_call_returns_is_not_lost(desk):
    desk.portal_up(MOCK_DELAY_MS=400)
    r = desk.plugin().call("screen.capture", {"outputPath": out(desk)})
    assert r["result"]["tool"] == "portal" and r["error"] is None


def test_each_capture_uses_its_own_request_token(desk):
    portal = desk.portal_up()
    p = desk.plugin()
    for i in range(3):
        desk.shot.write_bytes(tiny_png(3, 2))                            # the plugin removes it after each capture
        assert p.call("screen.capture", {"outputPath": out(desk, f"s{i}.png")})["error"] is None
    tokens = [c["params"].split("'handle_token': <'")[1].split("'")[0] for c in portal.calls("Screenshot")]
    assert len(set(tokens)) == 3


def test_user_declining_is_final_and_never_falls_back_to_another_tool(desk):
    desk.portal_up(MOCK_SCREENSHOT_CODE=1)
    desk.stub_tool("grim", '/bin/cp "$PAI_STUB_PNG" "$1"')               # would succeed if (wrongly) tried
    p = desk.plugin(PAI_STUB_PNG=str(desk.tmp / "stub.png"))
    (desk.tmp / "stub.png").write_bytes(tiny_png())
    r = p.call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] == "capture_denied" and "tool" not in r["result"]
    assert not (desk.out / "shot.png").exists()


def test_no_answer_to_the_dialog_times_out_without_fallback(desk):
    desk.portal_up(MOCK_NO_RESPONSE="Screenshot")
    desk.stub_tool("grim", '/bin/cp "$PAI_STUB_PNG" "$1"')
    (desk.tmp / "stub.png").write_bytes(tiny_png())
    r = desk.plugin(PAI_SCREEN_PORTAL_TIMEOUT=1, PAI_STUB_PNG=str(desk.tmp / "stub.png")).call(
        "screen.capture", {"outputPath": out(desk)})
    assert r["error"] == "capture_timeout"


def test_portal_failure_falls_back_to_a_command_line_tool_in_auto_mode(desk):
    desk.portal_up(MOCK_SCREENSHOT_CODE=2)
    desk.stub_tool("grim", '/bin/cp "$PAI_STUB_PNG" "$1"')
    (desk.tmp / "stub.png").write_bytes(tiny_png(5, 4))
    r = desk.plugin(PAI_STUB_PNG=str(desk.tmp / "stub.png")).call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] is None and r["result"]["tool"] == "grim" and r["result"]["width"] == "5"


def test_backend_portal_does_not_fall_back(desk):
    desk.portal_up(MOCK_SCREENSHOT_CODE=2)
    desk.stub_tool("grim", '/bin/cp "$PAI_STUB_PNG" "$1"')
    r = desk.plugin(PAI_SCREEN_BACKEND="portal").call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] == "capture_failed" and r["result"]["tool"] == "portal"


def test_no_portal_installed(desk):
    # nothing owns the portal bus name on the private bus
    p = desk.plugin(PAI_SCREEN_BACKEND="portal")
    r = p.call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] == "capture_failed" and r["result"]["portal_error"] == "portal_unavailable"
    desk.stub_tool("grim", '/bin/cp "$PAI_STUB_PNG" "$1"')
    (desk.tmp / "stub.png").write_bytes(tiny_png())
    r = desk.plugin(PAI_STUB_PNG=str(desk.tmp / "stub.png")).call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] is None and r["result"]["tool"] == "grim"           # auto mode finds another way


def test_nothing_available_at_all(desk):
    r = desk.plugin().call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] == "no_screen_tool_available" and r["result"]["portal_error"] == "portal_unavailable"


# ── command-line tools ──────────────────────────────────────────────────────

def test_a_tool_that_succeeds_without_producing_a_png_is_not_a_screenshot(desk):
    desk.stub_tool("grim", 'echo not an image > "$1"')
    r = desk.plugin(PAI_SCREEN_BACKEND="cli").call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] == "capture_invalid" and not (desk.out / "shot.png").exists()


def test_a_failing_tool_is_reported(desk):
    desk.stub_tool("grim", 'echo "compositor says no" >&2; exit 1')
    r = desk.plugin(PAI_SCREEN_BACKEND="cli").call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] == "capture_failed" and "compositor says no" in r["result"]["stderr"]


# ── gates and validation ────────────────────────────────────────────────────

def test_capture_needs_the_screen_consent(desk):
    desk.portal_up()
    r = desk.plugin(PAI_SCREEN_CONSENT="0").call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] == "screen_capture_not_consented"
    assert desk.portal.calls("Screenshot") == []                         # the portal was never even asked


@pytest.mark.parametrize("bad", ["/etc/pai_shot.png", "/root/x.png", "../../etc/x.png"])
def test_output_path_must_be_inside_the_allowed_roots(desk, bad):
    desk.portal_up()
    r = desk.plugin().call("screen.capture", {"outputPath": bad})
    assert r["error"] == "outputPath_outside_allowed_roots"
    assert desk.portal.calls("Screenshot") == []


def test_invalid_backend_setting(desk):
    r = desk.plugin(PAI_SCREEN_BACKEND="telepathy").call("screen.capture", {"outputPath": out(desk)})
    assert r["error"] == "invalid_backend"


def test_default_output_path_is_created_under_the_pictures_dir(desk):
    desk.portal_up()
    p = desk.plugin(PAI_SCREEN_DIR=str(desk.tmp / "pics" / "PAI"))
    r = p.call("screen.capture")
    assert r["error"] is None and r["result"]["path"].startswith(str(desk.tmp / "pics" / "PAI") + "/screen_")
