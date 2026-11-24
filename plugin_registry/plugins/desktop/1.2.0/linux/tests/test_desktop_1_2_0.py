"""Hermetic tests for the Linux desktop plugin (libpai_desktop.so).

Run from the repo root:
    .venv/bin/python -m pytest plugin_registry/plugins/desktop/1.1.0/linux/tests -q

The real library is loaded through the C ABI (tools/pai_test.py, same as the host does) in a
subprocess whose PATH contains ONLY stub programs created per test. The stubs record the exact
argv/stdin they receive, so the tests prove what the plugin would run without showing a
notification, playing sound, changing the volume or opening a window, and they can exercise the
"tool is not installed" paths. Override the library with PAI_DESKTOP_SO.
"""
from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LINUX_DIR = HERE.parent
REGISTRY_ROOT = HERE.parents[4]                       # .../plugin_registry
PAI_TEST = REGISTRY_ROOT / "tools" / "pai_test.py"
SO = Path(os.environ.get("PAI_DESKTOP_SO", LINUX_DIR / "artifact" / "libpai_desktop.so"))
MANIFEST = json.loads((LINUX_DIR / "manifest.json").read_text())

pytestmark = pytest.mark.skipif(not SO.exists(), reason=f"{SO} not built")

# Every stub appends its argv (one arg per line) to <log>/<name>.args and exits with
# $STUB_RC_<NAME> (default 0). Absolute paths only: PATH holds nothing but the stubs.
_STUB = """#!/bin/sh
LOG="$STUB_LOG"; N="{name}"
T="$LOG/$N.args.$$"; : > "$T"
for a in "$@"; do /usr/bin/printf '%s\\n' "$a" >> "$T"; done
/bin/mv "$T" "$LOG/$N.args"        # atomic: a reader never sees a half-written file
{body}
RC="$(/usr/bin/printenv STUB_RC_{var})"
exit "${{RC:-0}}"
"""
_BODIES = {
    # clipboard: write stores stdin (+ its permission bits), read replays it
    "xclip": """case " $* " in
  *" -o "*) [ -f "$LOG/clip.txt" ] && /bin/cat "$LOG/clip.txt" || { echo "Error: target STRING not available" >&2; exit 1; } ;;
  *) /usr/bin/stat -L -c %a /dev/stdin > "$LOG/clip.mode"; /bin/cat > "$LOG/clip.txt" ;;
esac""",
    "wpctl": '''case "$1" in get-volume) echo "Volume: 0.50";; esac''',
    "playerctl": '''case "$1" in status) echo Playing;; esac
[ "$(/usr/bin/printenv STUB_NO_PLAYER)" = 1 ] && { echo "No players found" >&2; exit 1; }''',
    "dunstctl": '''case "$1" in history) echo '{"data":[[]]}';; esac''',
    "notify-send": "",  "mpv": "", "ffplay": "", "spd-say": "", "xdg-open": "",
    "gtk-launch": "", "gio": "", "pai-fake-app": "",
    # xdg-mime: `query filetype <f>` -> text/plain, `query default <mime>` -> the Fake app
    "xdg-mime": '''case "$1 $2" in "query filetype") echo text/plain;; "query default") echo org.example.Fake.desktop;; esac''',
}


def _var(name: str) -> str:
    return name.upper().replace("-", "_")


class Plugin:
    def __init__(self, tmp: Path, stubs: list[str], extra_env: dict | None):
        self.extra_pids = []
        self.log = tmp / "log"
        self.bin = tmp / "bin"
        self.log.mkdir()
        self.bin.mkdir()
        for name in stubs:
            f = self.bin / name
            f.write_text(_STUB.format(name=name, var=_var(name), body=_BODIES.get(name, "")))
            f.chmod(f.stat().st_mode | stat.S_IXUSR)
        self.env = {
            "PATH": str(self.bin), "STUB_LOG": str(self.log), "HOME": str(tmp),
            "XDG_SESSION_TYPE": "x11", "XDG_DATA_HOME": str(tmp / "nodata"),
            "XDG_DATA_DIRS": str(tmp / "nodata"), "XDG_RUNTIME_DIR": str(tmp),
            **(extra_env or {}),
        }

    def _run(self, *args: str, env: dict | None = None) -> dict:
        p = subprocess.run([sys.executable, "-I", str(PAI_TEST), str(SO), *args],
                           capture_output=True, text=True, env={**self.env, **(env or {})}, timeout=60)
        assert p.stdout.strip(), p.stderr
        return json.loads(p.stdout)

    def call(self, cap: str, params: dict | None = None, env: dict | None = None) -> dict:
        d = self._run("--capability", cap, "--params", json.dumps(params or {}), env=env)
        d["result"] = d.get("result") or {}
        return d

    def describe(self) -> dict:
        return self._run("--describe")

    def args(self, name: str, wait: float = 3.0) -> list[str]:
        """argv the stub received (waits for detached spawns)."""
        f = self.log / f"{name}.args"
        end = time.time() + wait
        while not f.exists() and time.time() < end:
            time.sleep(0.05)
        assert f.exists(), f"{name} was never run"
        return f.read_text().splitlines()

    def add_script(self, name: str, body: str) -> Path:
        """An executable 'app' on PATH. Scripts use absolute tool paths (PATH is only the stubs)."""
        f = self.bin / name
        f.write_text("#!/bin/sh\n" + body + "\n")
        f.chmod(0o755)
        return f

    def add_desktop(self, desktop_id: str, name: str, exec_line: str, terminal: bool = False) -> None:
        apps = Path(self.env["XDG_DATA_DIRS"]) / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        (apps / f"{desktop_id}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\n"
            f"Terminal={'true' if terminal else 'false'}\n")
        self.env["XDG_DATA_DIRS"] = str(Path(self.env["XDG_DATA_DIRS"]))

    @property
    def store(self) -> Path:
        return Path(self.env["XDG_RUNTIME_DIR"]) / "pai_desktop_opens"

    def kill_everything(self) -> None:
        pids = [int(l.split("\t")[1]) for l in self.store.read_text().splitlines()] if self.store.exists() else []
        pids += [int(x) for x in self.extra_pids]
        for pid in pids:
            for target in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
                try:
                    target()
                except (ProcessLookupError, PermissionError):
                    pass

    extra_pids: list = []

    def ran(self, name: str, wait: float = 0.4) -> bool:
        time.sleep(wait)
        return (self.log / f"{name}.args").exists()


@pytest.fixture
def make(tmp_path):
    n = 0
    made: list[Plugin] = []

    def _make(*stubs: str, **env) -> Plugin:
        nonlocal n
        n += 1                       # a test may build several plugins: one sandbox each
        sandbox = tmp_path / f"sandbox{n}"
        sandbox.mkdir()
        p = Plugin(sandbox, list(stubs), env)
        made.append(p)
        return p
    yield _make
    for p in made:                   # never leave a test app running
        p.kill_everything()


# ── registration / manifest consistency ─────────────────────────────────────

def test_library_and_manifest_agree(make):
    d = make().describe()
    assert d["name"] == "pai.desktop" == MANIFEST["runtime"]["nativeModule"]
    assert sorted(d["capabilities"]) == sorted(c["id"] for c in MANIFEST["capabilities"])
    assert len(d["capabilities"]) == 13
    assert set(d["permissions"]) >= set(MANIFEST["permissions"])


def test_unknown_capability(make):
    assert make().call("nope.nothing")["error"] == "unknown_capability"


# ── clipboard.read / clipboard.write ────────────────────────────────────────

def test_clipboard_roundtrip_unicode_and_private_tempfile(make):
    p = make("xclip")
    r = p.call("clipboard.write", {"content": "héllo ✓\nline2"})
    assert r["error"] is None and r["result"]["ok"] == "true"
    assert r["result"]["bytes_written"] == str(len("héllo ✓\nline2".encode()))
    assert (p.log / "clip.mode").read_text().strip() == "600"          # not world-readable
    assert not list(Path(p.env["XDG_RUNTIME_DIR"]).glob("pai_clip_*"))  # cleaned up
    r = p.call("clipboard.read")
    assert r["result"]["content"] == "héllo ✓\nline2" and r["result"]["ok"] == "true"


def test_clipboard_write_requires_content(make):
    assert make("xclip").call("clipboard.write")["error"] == "missing_required_param: content"


def test_clipboard_write_tool_failure(make):
    r = make("xclip").call("clipboard.write", {"content": "x"}, env={"STUB_RC_XCLIP": "1"})
    assert r["error"] == "clipboard_write_failed" and r["result"]["ok"] == "false"


def test_clipboard_empty_is_not_an_error(make):
    r = make("xclip").call("clipboard.read", env={"STUB_RC_XCLIP": "1"})
    assert r["error"] is None and r["result"]["content"] == ""


def test_clipboard_no_tool(make):
    assert make().call("clipboard.read")["error"] == "no_clipboard_tool"
    assert make().call("clipboard.write", {"content": "x"})["error"] == "no_clipboard_tool"


# ── audio.play ──────────────────────────────────────────────────────────────

def test_audio_play_local_file_uses_preferred_player(make, tmp_path):
    f = tmp_path / "a b.wav"
    f.write_bytes(b"RIFF")
    p = make("mpv", "ffplay")
    r = p.call("audio.play", {"path": str(f)})
    assert r["result"]["tool"] == "mpv" and r["result"]["mode"] == "async"
    assert p.args("mpv")[-1] == str(f)                                   # one argv entry, space intact


def test_audio_play_falls_back_to_next_player(make, tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    p = make("ffplay")
    assert p.call("audio.play", {"path": str(f)})["result"]["tool"] == "ffplay"
    assert "-nodisp" in p.args("ffplay")


def test_audio_play_url_skips_file_check(make):
    p = make("mpv")
    assert p.call("audio.play", {"url": "https://example.com/a.mp3"})["error"] is None
    assert p.args("mpv")[-1] == "https://example.com/a.mp3"


@pytest.mark.parametrize("src", ["--script=/tmp/evil.lua", "-x", "--"])
def test_audio_play_rejects_option_injection(make, src):
    p = make("mpv")
    r = p.call("audio.play", {"path": src})
    assert r["error"] == "invalid_source"
    assert not p.ran("mpv")                                              # player never started


def test_audio_play_missing_file_and_params_and_tool(make, tmp_path):
    assert make("mpv").call("audio.play", {"path": str(tmp_path / "no.mp3")})["error"] == "file_not_found"
    assert make("mpv").call("audio.play")["error"] == "missing url or path"
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    assert make().call("audio.play", {"path": str(f)})["error"] == "no_audio_tool"


# ── audio.speak ─────────────────────────────────────────────────────────────

def test_audio_speak(make):
    p = make("spd-say")
    r = p.call("audio.speak", {"text": "hello world"})
    assert r["error"] is None and r["result"]["tool"] == "spd-say"
    assert p.args("spd-say") == ["hello world"]


def test_audio_speak_validation_and_failure(make):
    assert make("spd-say").call("audio.speak")["error"] == "missing_required_param: text"
    assert make("spd-say").call("audio.speak", {"text": "x" * 40000})["error"] == "text_too_long"
    assert make().call("audio.speak", {"text": "x"})["error"] == "no_tts_tool"
    r = make("spd-say").call("audio.speak", {"text": "x"}, env={"STUB_RC_SPD_SAY": "2"})
    assert r["error"] == "tts_failed"


# ── audio.volume ────────────────────────────────────────────────────────────

def test_volume_get_set_mute_unmute(make):
    p = make("wpctl")
    r = p.call("audio.volume", {"action": "get"})
    assert r["result"]["volume"] == "Volume: 0.50"
    assert p.args("wpctl") == ["get-volume", "@DEFAULT_AUDIO_SINK@"]
    p.call("audio.volume", {"action": "set", "level": 30})
    assert p.args("wpctl")[:2] == ["set-volume", "@DEFAULT_AUDIO_SINK@"]
    assert float(p.args("wpctl")[2]) == pytest.approx(0.30)
    p.call("audio.volume", {"action": "mute"})
    assert p.args("wpctl") == ["set-mute", "@DEFAULT_AUDIO_SINK@", "1"]
    p.call("audio.volume", {"action": "unmute"})
    assert p.args("wpctl") == ["set-mute", "@DEFAULT_AUDIO_SINK@", "0"]


def test_volume_default_action_is_get(make):
    assert make("wpctl").call("audio.volume")["result"]["action"] == "get"


def test_volume_falls_back_to_pactl(make):
    p = make("pactl")
    p.call("audio.volume", {"action": "set", "level": 45})
    assert p.args("pactl") == ["set-sink-volume", "@DEFAULT_SINK@", "45%"]


@pytest.mark.parametrize("level", [-1, 101, 150, "abc", "", "12x"])
def test_volume_rejects_bad_level_without_touching_audio(make, level):
    p = make("wpctl")
    assert p.call("audio.volume", {"action": "set", "level": level})["error"] == "invalid_level"
    assert not p.ran("wpctl")


def test_volume_set_needs_level_and_bounds_are_inclusive(make):
    p = make("wpctl")
    assert p.call("audio.volume", {"action": "set"})["error"] == "invalid_level"
    assert p.call("audio.volume", {"action": "set", "level": 0})["error"] is None
    assert p.call("audio.volume", {"action": "set", "level": 100})["error"] is None


def test_volume_errors(make):
    assert make("wpctl").call("audio.volume", {"action": "explode"})["error"] == "invalid_action"
    assert make().call("audio.volume")["error"] == "no_audio_control_tool"
    r = make("wpctl").call("audio.volume", env={"STUB_RC_WPCTL": "1"})
    assert r["error"] == "volume_control_failed"


# ── media.control ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["play", "pause", "play-pause", "stop", "next", "previous"])
def test_media_actions(make, action):
    p = make("playerctl")
    assert p.call("media.control", {"action": action})["result"]["ok"] == "true"
    assert p.args("playerctl") == [action]


def test_media_status_and_no_player(make):
    assert make("playerctl").call("media.control", {"action": "status"})["result"]["stdout"] == "Playing"
    for action in ("status", "play-pause"):
        r = make("playerctl").call("media.control", {"action": action},
                                   env={"STUB_NO_PLAYER": "1", "STUB_RC_PLAYERCTL": "1"})
        assert r["error"] == "no_player" and r["result"]["ok"] == "false"


def test_media_validation(make):
    p = make("playerctl")
    assert p.call("media.control", {"action": "rm -rf /"})["error"] == "invalid_action"
    assert not p.ran("playerctl")
    assert make().call("media.control", {"action": "play"})["error"] == "playerctl_not_installed"


# ── notifications ───────────────────────────────────────────────────────────

def test_notification_send_full(make):
    p = make("notify-send")
    r = p.call("notifications.send", {"title": "T", "message": "M", "urgency": "low", "timeout_ms": 3000})
    assert r["error"] is None and r["result"]["urgency"] == "low"
    assert p.args("notify-send") == ["-u", "low", "-a", "PAI", "-t", "3000", "--", "T", "M"]


def test_notification_send_defaults_and_single_field(make):
    p = make("notify-send")
    p.call("notifications.send", {"message": "only message"})
    assert p.args("notify-send") == ["-u", "normal", "-a", "PAI", "--", "only message"]


def test_notification_title_that_looks_like_a_flag_is_text(make):
    p = make("notify-send")
    p.call("notifications.send", {"title": "--help", "message": "-u"})
    a = p.args("notify-send")
    assert a.index("--") < a.index("--help") and a[-2:] == ["--help", "-u"]


@pytest.mark.parametrize("params,err", [
    ({}, "missing_required_param: title or message"),
    ({"title": "x", "urgency": "loud"}, "invalid_urgency"),
    ({"title": "x", "timeout_ms": "abc"}, "invalid_timeout_ms"),
    ({"title": "x", "timeout_ms": -1}, "invalid_timeout_ms"),
    ({"title": "x", "timeout_ms": 600001}, "invalid_timeout_ms"),
    ({"title": "x" * 5000}, "text_too_long"),
])
def test_notification_send_validation(make, params, err):
    p = make("notify-send")
    assert p.call("notifications.send", params)["error"] == err
    assert not p.ran("notify-send")


def test_notification_send_tool_missing_or_failing(make):
    assert make().call("notifications.send", {"title": "x"})["error"] == "notify_send_not_installed"
    r = make("notify-send").call("notifications.send", {"title": "x"}, env={"STUB_RC_NOTIFY_SEND": "1"})
    assert r["error"] == "notify_failed"


def test_notifications_read_and_dismiss_with_dunst(make):
    p = make("dunstctl")
    r = p.call("notifications.read")
    assert r["error"] is None and r["result"]["format"] == "json" and "data" in r["result"]["notifications"]
    assert p.args("dunstctl") == ["history"]
    assert p.call("notifications.dismiss")["result"]["ok"] == "true"
    assert p.args("dunstctl") == ["close-all"]


def test_notifications_read_and_dismiss_without_dunst(make):
    p = make()
    assert p.call("notifications.read")["error"] == "not_supported"
    assert p.call("notifications.dismiss")["error"] == "no_notification_daemon_detected"


def test_notifications_read_failure(make):
    r = make("dunstctl").call("notifications.read", env={"STUB_RC_DUNSTCTL": "1"})
    assert r["error"] == "notifications_read_failed"


# ── app.launch ──────────────────────────────────────────────────────────────

def test_launch_url(make):
    p = make("xdg-open")
    r = p.call("app.launch", {"url": "https://example.com/x?a=1&b=2"})
    assert r["result"]["tool"] == "xdg-open"
    assert p.args("xdg-open") == ["https://example.com/x?a=1&b=2"]


def test_launch_file_url_with_percent_encoded_spaces(make, tmp_path):
    d = tmp_path / "my dir"; d.mkdir()
    (d / "pic 1.jpg").write_bytes(b"x")
    p = make("xdg-open")
    url = f"file://{tmp_path}/my%20dir/pic%201.jpg"
    assert p.call("app.launch", {"url": url})["error"] is None
    assert p.args("xdg-open") == [url]                                   # original URL passed through


def test_launch_file_url_missing_or_malformed(make, tmp_path):
    p = make("xdg-open")
    assert p.call("app.launch", {"url": f"file://{tmp_path}/gone%20x.txt"})["error"] == "file_not_found"
    assert p.call("app.launch", {"url": "file:///tmp/%zz"})["error"] == "invalid_url"
    assert p.call("app.launch", {"url": "file:///tmp/%2"})["error"] == "invalid_url"
    assert p.call("app.launch", {"url": "file:///tmp/%00x"})["error"] == "invalid_url"
    assert not p.ran("xdg-open")


def test_launch_rejects_option_like_values(make):
    p = make("xdg-open", "gtk-launch", "pai-fake-app")
    assert p.call("app.launch", {"url": "--version"})["error"] == "invalid_url"
    assert p.call("app.launch", {"app": "-x"})["error"] == "invalid_app"
    assert not p.ran("xdg-open") and not p.ran("gtk-launch")


def test_launch_app_via_gtk_launch(make):
    p = make("gtk-launch")
    r = p.call("app.launch", {"app": "org.example.App"})
    assert r["result"]["tool"] == "gtk-launch" and p.args("gtk-launch") == ["org.example.App"]


def test_launch_app_falls_back_to_gio_when_gtk_launch_is_broken(make, tmp_path):
    apps = tmp_path / "share" / "applications"; apps.mkdir(parents=True)
    desktop = apps / "org.example.App.desktop"
    desktop.write_text("[Desktop Entry]\nType=Application\nName=X\nExec=true\n")
    p = make("gtk-launch", "gio", XDG_DATA_DIRS=str(tmp_path / "share"), STUB_RC_GTK_LAUNCH="133")
    r = p.call("app.launch", {"app": "org.example.App"})
    assert r["result"]["tool"] == "gio"
    assert p.args("gio") == ["launch", str(desktop)]


def test_launch_app_direct_exec_and_not_found(make):
    p = make("pai-fake-app")
    assert p.call("app.launch", {"app": "pai-fake-app"})["result"]["tool"] == "direct"
    assert p.args("pai-fake-app") == []
    r = make().call("app.launch", {"app": "definitely-not-an-app-xyz"})
    assert r["error"] == "app_not_found" and r["result"]["ok"] == "false"   # no false success
    assert make().call("app.launch", {"app": "/nonexistent/bin/x"})["error"] == "app_not_found"


def test_launch_requires_app_or_url(make):
    assert make().call("app.launch")["error"] == "missing app or url"


# ── app.launch across file types ────────────────────────────────────────────

EXTENSIONS = [
    "jpg", "jpeg", "png", "gif", "webp", "svg", "bmp",            # images (the manifest's own example)
    "pdf", "txt", "md", "csv", "json", "log",                      # documents / text
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods",    # office
    "mp3", "wav", "flac", "ogg", "mp4", "mkv", "webm", "avi",     # audio / video
    "zip", "tar", "gz", "html", "htm", "py", "sh", "JPG", "Tar.GZ",
]


@pytest.mark.parametrize("ext", EXTENSIONS)
def test_launch_local_file_of_any_extension(make, tmp_path, ext):
    f = tmp_path / f"my file.{ext}"
    f.write_bytes(b"x")
    p = make("xdg-open")
    url = f"file://{str(f).replace(' ', '%20')}"
    r = p.call("app.launch", {"url": url})
    assert r["error"] is None and r["result"]["ok"] == "true", r
    assert p.args("xdg-open") == [url]           # extension never changes how it is handed over


@pytest.mark.parametrize("name", ["naïve ünï.png", "日本語.pdf", "a#b.txt", "a&b=c.txt", "no_extension", ".hidden"])
def test_launch_unusual_file_names(make, tmp_path, name):
    from urllib.parse import quote
    f = tmp_path / name
    f.write_bytes(b"x")
    p = make("xdg-open")
    url = "file://" + quote(str(f))
    assert p.call("app.launch", {"url": url})["error"] is None
    assert p.args("xdg-open") == [url]


@pytest.mark.parametrize("url", ["https://example.com", "http://example.com/a?b=1#c", "mailto:a@b.co",
                                 "tel:+15551234", "ftp://example.com/f.zip"])
def test_launch_non_file_urls(make, url):
    p = make("xdg-open")
    assert p.call("app.launch", {"url": url})["error"] is None
    assert p.args("xdg-open") == [url]


# ── app.open / app.close / app.list ─────────────────────────────────────────

def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except (FileNotFoundError, ProcessLookupError):
        return False


def _gone(pid: int, wait: float = 5.0) -> bool:
    end = time.time() + wait
    while time.time() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _fake_app(p: Plugin, name: str = "fakeapp") -> None:
    """A long-lived app: logs its args, then becomes `sleep`."""
    p.add_script(name, f'''/usr/bin/printf '%s\\n' "$@" > "$STUB_LOG/{name}.argv"
exec /bin/sleep 120''')


def _open(p: Plugin, **params) -> dict:
    r = p.call("app.open", params)
    if r["result"].get("pid"):
        p.extra_pids.append(r["result"]["pid"])
    return r


def _listed(p: Plugin) -> list[dict]:
    r = p.call("app.list")
    assert r["error"] is None and r["result"]["ok"] == "true"
    apps = json.loads(r["result"]["apps"])
    assert len(apps) == int(r["result"]["count"])
    return apps


def test_open_program_is_tracked_and_is_its_own_session(make):
    p = make()
    _fake_app(p)
    r = _open(p, app="fakeapp")
    res = r["result"]
    assert r["error"] is None and res["ok"] == "true" and res["closable"] == "true"
    assert res["handle"].startswith("open-")
    pid = int(res["pid"])
    assert _alive(pid)
    assert os.getsid(pid) == pid and os.getpgid(pid) == pid      # own session: closable as a group
    apps = _listed(p)
    assert [a["handle"] for a in apps] == [res["handle"]] and apps[0]["pid"] == pid
    assert oct(p.store.stat().st_mode & 0o777) == "0o600"        # private record file


def test_open_via_desktop_entry_with_url_and_name_lookups(make):
    p = make()
    _fake_app(p)
    p.add_desktop("org.example.Fake", "Fake App", "fakeapp --flag %U")
    for query in ("org.example.Fake", "Fake App", "fake app", "fake", "org.example.Fake.desktop"):
        r = _open(p, app=query, url="https://example.com/x?a=1")
        assert r["result"]["closable"] == "true", (query, r)
        assert r["result"]["app"] == "Fake App"
    argv = (p.log / "fakeapp.argv").read_text().splitlines()
    assert argv == ["--flag", "https://example.com/x?a=1"]
    assert len(_listed(p)) == 5


def test_open_local_file_decodes_path_for_percent_f(make, tmp_path):
    f = tmp_path / "my dir" / "a b.txt"
    f.parent.mkdir()
    f.write_text("x")
    p = make()
    _fake_app(p)
    p.add_desktop("org.example.Fake", "Fake App", "fakeapp %f")
    assert _open(p, app="fake", url=f"file://{tmp_path}/my%20dir/a%20b.txt")["result"]["closable"] == "true"
    assert (p.log / "fakeapp.argv").read_text().splitlines() == [str(f)]
    # plain absolute path works too
    assert _open(p, app="fake", url=str(f))["result"]["closable"] == "true"


def test_open_url_only_uses_the_default_handler(make, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("x")
    p = make("xdg-mime")
    _fake_app(p)
    p.add_desktop("org.example.Fake", "Fake App", "fakeapp %U")
    r = _open(p, url=str(f))
    assert r["result"]["closable"] == "true" and r["result"]["app"] == "Fake App"
    assert (p.log / "fakeapp.argv").read_text().splitlines() == [f"file://{f}"]
    assert _open(p, url="https://example.com")["result"]["app"] == "Fake App"   # scheme handler


def test_open_url_with_no_default_handler(make, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("x")
    p = make()
    assert p.call("app.open", {"url": str(f)})["error"] == "no_handler"


def test_open_that_hands_off_is_reported_as_not_closable(make):
    p = make()
    p.add_script("quickapp", "exit 0")                              # an app that forwards and exits
    r = p.call("app.open", {"app": "quickapp"})
    assert r["error"] is None and r["result"]["closable"] == "false"
    assert r["result"]["status"] == "handed_off_or_exited" and "handle" not in r["result"]
    assert _listed(p) == []                                         # nothing PAI could close is recorded


def test_open_errors(make, tmp_path):
    p = make()
    assert p.call("app.open")["error"] == "missing app or url"
    assert p.call("app.open", {"app": "-x"})["error"] == "invalid_app"
    assert p.call("app.open", {"url": "-x"})["error"] == "invalid_url"
    assert p.call("app.open", {"url": f"file://{tmp_path}/gone.txt"})["error"] == "file_not_found"
    assert p.call("app.open", {"url": "relative/path.txt"})["error"] == "invalid_url"
    assert p.call("app.open", {"app": "no-such-app-xyz"})["error"] == "app_not_found"
    bad = p.add_script("badshebang", "")
    bad.write_text("#!/nonexistent/interpreter\n")
    assert p.call("app.open", {"app": "badshebang"})["error"] == "exec_failed"
    p.add_desktop("org.example.Term", "Term App", "fakeapp", terminal=True)
    assert p.call("app.open", {"app": "Term App"})["error"] == "terminal_app_unsupported"
    assert _listed(p) == []


def test_close_by_handle_ends_the_whole_app_including_children(make):
    p = make()
    p.add_script("treeapp", '''/bin/sleep 120 &
echo $! > "$STUB_LOG/child.pid"
wait''')
    r = _open(p, app="treeapp")
    pid, handle = int(r["result"]["pid"]), r["result"]["handle"]
    time.sleep(0.3)
    child = int((p.log / "child.pid").read_text())
    assert _alive(pid) and _alive(child)
    c = p.call("app.close", {"handle": handle})
    assert c["error"] is None and c["result"]["ok"] == "true" and c["result"]["closed"] == handle
    assert _gone(pid) and _gone(child)                              # the group, not just the leader
    assert _listed(p) == []


def test_close_by_name_without_params_and_ambiguity(make):
    p = make()
    _fake_app(p, "appone")
    _fake_app(p, "apptwo")
    one = _open(p, app="appone")["result"]
    assert p.call("app.close", {"app": "APPONE"})["result"]["ok"] == "true"     # case-insensitive
    assert _gone(int(one["pid"]))
    two = _open(p, app="apptwo")["result"]
    assert p.call("app.close")["result"]["closed"] == two["handle"]              # the only open app
    assert _gone(int(two["pid"]))
    _open(p, app="appone")
    _open(p, app="apptwo")
    r = p.call("app.close")
    assert r["error"] == "ambiguous" and len(json.loads(r["result"]["apps"])) == 2
    assert len(_listed(p)) == 2                                                  # nothing was closed


def test_close_unknown_and_nothing_open(make):
    p = make()
    assert p.call("app.close")["error"] == "nothing_open"
    _fake_app(p)
    _open(p, app="fakeapp")
    assert p.call("app.close", {"handle": "open-deadbeef"})["error"] == "not_found"
    assert p.call("app.close", {"app": "something-else"})["error"] == "not_found"
    assert len(_listed(p)) == 1


def test_close_when_the_app_is_already_gone_is_success(make):
    p = make()
    _fake_app(p)
    r = _open(p, app="fakeapp")["result"]
    os.killpg(int(r["pid"]), signal.SIGKILL)
    assert _gone(int(r["pid"]))
    c = p.call("app.close", {"handle": r["handle"]})
    assert c["error"] is None and c["result"]["status"] == "already_closed"


def test_close_never_touches_apps_pai_did_not_open(make):
    p = make()
    _fake_app(p)
    stranger = subprocess.Popen(["/bin/sleep", "120"], start_new_session=True)
    p.extra_pids.append(stranger.pid)
    try:
        r = p.call("app.close", {"app": "sleep"})
        assert r["error"] in ("not_found", "nothing_open")
        assert p.call("app.close", {"handle": str(stranger.pid)})["error"] in ("not_found", "nothing_open")
        assert _alive(stranger.pid)
        # and an app started with app.launch is never closable
        p.add_script("launched", '''echo $$ > "$STUB_LOG/launched.pid"
exec /bin/sleep 120''')
        assert p.call("app.launch", {"app": "launched"})["result"]["closable"] == "false"
        end = time.time() + 3
        while not (p.log / "launched.pid").exists() and time.time() < end:
            time.sleep(0.05)
        p.extra_pids.append(int((p.log / "launched.pid").read_text()))   # not tracked by PAI: we clean up
        assert p.call("app.close", {"app": "launched"})["error"] in ("not_found", "nothing_open")
    finally:
        stranger.kill()
        stranger.wait()


def test_close_needs_force_for_an_app_that_ignores_sigterm(make):
    p = make()
    p.add_script("stubborn", "trap '' TERM\nwhile :; do /bin/sleep 0.2; done")
    r = _open(p, app="stubborn")["result"]
    pid = int(r["pid"])
    soft = p.call("app.close", {"handle": r["handle"]})
    assert soft["error"] == "did_not_close" and soft["result"]["ok"] == "false"
    assert soft["result"]["still_running"] == r["handle"] and "force" in soft["result"]["hint"]
    assert _alive(pid) and len(_listed(p)) == 1                     # still tracked, retry possible
    hard = p.call("app.close", {"handle": r["handle"], "force": True})
    assert hard["error"] is None and hard["result"]["forced"] == "true"
    assert _gone(pid) and _listed(p) == []


def test_recycled_pid_is_never_signalled(make):
    p = make()
    _fake_app(p)
    r = _open(p, app="fakeapp")["result"]
    pid = int(r["pid"])
    lines = p.store.read_text().splitlines()
    parts = lines[0].split("\t")
    parts[2] = str(int(parts[2]) + 1)                               # start time no longer matches
    p.store.write_text("\t".join(parts) + "\n")
    p.store.chmod(0o600)
    c = p.call("app.close", {"handle": r["handle"]})
    assert c["error"] is None and c["result"]["status"] == "already_closed"   # "not the app we opened"
    assert _alive(pid)                                              # ...and it was never signalled
    assert _listed(p) == []                                         # the stale record is dropped
    assert _alive(pid)


def test_untrusted_record_file_is_ignored(make):
    p = make()
    _fake_app(p)
    r = _open(p, app="fakeapp")["result"]
    p.store.chmod(0o644)                                            # group/world readable: not ours
    assert _listed(p) == []
    assert p.call("app.close", {"handle": r["handle"]})["error"] in ("not_found", "nothing_open")
    assert _alive(int(r["pid"]))


def test_open_apps_are_capped(make):
    p = make()
    _fake_app(p)
    rows = []
    for i in range(32):                                             # fabricate 32 live records
        proc = subprocess.Popen(["/bin/sleep", "120"], start_new_session=True)
        p.extra_pids.append(proc.pid)
        with open(f"/proc/{proc.pid}/stat") as f:
            start = f.read().rsplit(")", 1)[1].split()[19]
        rows.append(f"open-{i:08x}\t{proc.pid}\t{start}\t{int(time.time())}\tfake\t")
    p.store.write_text("\n".join(rows) + "\n")
    p.store.chmod(0o600)
    assert p.call("app.open", {"app": "fakeapp"})["error"] == "too_many_open_apps"


def test_launch_is_fire_and_forget_and_says_so(make):
    p = make("xdg-open")
    assert p.call("app.launch", {"url": "https://example.com"})["result"]["closable"] == "false"
