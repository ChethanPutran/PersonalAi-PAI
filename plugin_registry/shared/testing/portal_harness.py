"""Test harness for plugins that talk to xdg-desktop-portal.

* PrivateBus   - a throw-away D-Bus session bus with NO service activation (so "no portal installed"
                 is real, and the real portal is never started or touched).
* MockPortal   - shared/testing/mock_portal.cpp (built once per test session) on that bus; every call
                 it receives is logged and readable with .calls().
* PluginSession - one plugin .so driven for several calls in ONE process (tools/pai_session.py),
                 as the real host does: state such as an approved input session lives across calls.

Nothing here talks to the user's real desktop.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

TESTING_DIR = Path(__file__).resolve().parent
REGISTRY_ROOT = TESTING_DIR.parents[1]
SESSION_DRIVER = REGISTRY_ROOT / "tools" / "pai_session.py"
MOCK_SOURCE = TESTING_DIR / "mock_portal.cpp"

_BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>session</type><keep_umask/><listen>unix:tmpdir=/tmp</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/><allow own="*"/></policy>
</busconfig>"""

_mock_binary: Optional[Path] = None


def mock_binary() -> Path:
    """Compile the mock portal once; skip the calling test if the toolchain is not there."""
    global _mock_binary
    if _mock_binary and _mock_binary.exists():
        return _mock_binary
    for tool in ("g++", "pkg-config", "dbus-daemon"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} not available")
    flags = subprocess.run(["pkg-config", "--cflags", "--libs", "gio-2.0"], capture_output=True, text=True)
    if flags.returncode != 0:
        pytest.skip("gio-2.0 development files not available")
    out = Path(tempfile.mkdtemp(prefix="pai_mock_portal_")) / "mock_portal"
    build = subprocess.run(["g++", "-std=c++17", "-o", str(out), str(MOCK_SOURCE), *flags.stdout.split()],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    _mock_binary = out
    return out


def tiny_png(width: int = 3, height: int = 2) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class PrivateBus:
    def __init__(self, workdir: Path) -> None:
        conf = workdir / "bus.conf"
        conf.write_text(_BUS_CONFIG)
        self.proc = subprocess.Popen(["dbus-daemon", f"--config-file={conf}", "--nofork", "--print-address"],
                                     stdout=subprocess.PIPE, text=True)
        self.address = self.proc.stdout.readline().strip()
        assert self.address.startswith("unix:"), "private dbus-daemon did not start"

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class MockPortal:
    def __init__(self, bus: PrivateBus, workdir: Path, **env: str) -> None:
        self.log = workdir / "mock.log"
        e = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": bus.address, "MOCK_LOG": str(self.log),
             **{k: str(v) for k, v in env.items()}}
        self.proc = subprocess.Popen([str(mock_binary())], env=e, stdout=subprocess.PIPE, text=True)
        assert self.proc.stdout.readline().strip() == "READY", "mock portal did not start"

    def calls(self, method: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]
        return [r for r in rows if method is None or r["method"] == method]

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class PluginSession:
    """One plugin library in one process; ``call`` may be used any number of times."""

    def __init__(self, so: Path, env: Dict[str, str], timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.proc = subprocess.Popen([sys.executable, "-I", str(SESSION_DRIVER), str(so)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def call(self, capability: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.proc.stdin.write(json.dumps({"capability": capability, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError(f"plugin process died: {self.proc.stderr.read()[-500:]}")
        out = json.loads(line)
        out["result"] = out.get("result") or {}
        return out

    def setenv(self, **env: object) -> None:
        """Change the plugin process's environment between calls (None unsets)."""
        self.proc.stdin.write(json.dumps({"env": {k: (None if v is None else str(v)) for k, v in env.items()}}) + "\n")
        self.proc.stdin.flush()
        self.proc.stdout.readline()

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(self.timeout)
        except Exception:
            self.proc.kill()


def base_env(bus: Optional[PrivateBus], workdir: Path, **extra: str) -> Dict[str, str]:
    """Environment for a plugin process: its own bus, its own HOME, nothing from the real desktop."""
    env = {"PATH": str(workdir / "bin"), "HOME": str(workdir), "XDG_RUNTIME_DIR": str(workdir),
           "XDG_SESSION_TYPE": "wayland", "LANG": "C.UTF-8"}
    (workdir / "bin").mkdir(exist_ok=True)
    if bus is not None:
        env["DBUS_SESSION_BUS_ADDRESS"] = bus.address
    env.update({k: str(v) for k, v in extra.items()})
    return env
