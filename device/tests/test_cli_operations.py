"""Following a remote install/uninstall: live stages, plain failure text, timeout, --no-wait."""
import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from cli_kit import Routes, make_client, make_ctx  # noqa: E402

from pai_device import cli  # noqa: E402
from pai_device.cli_support import follow_operation  # noqa: E402


def op(status, **extra):
    return {"id": "op1", "status": status, "terminal": status in ("succeeded", "failed"),
            "plugin_id": "camera", "device_id": "dev-1", "kind": "install", **extra}


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    async def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


async def follow(routes, **kw):
    lines = []
    clock = Clock()
    code = await follow_operation(make_client(routes), "op1", label="installing camera",
                                  sleep=clock.sleep, clock=clock.now, out=lines.append, **kw)
    return code, lines, clock


@pytest.mark.asyncio
async def test_success_prints_each_stage_once_and_backs_off():
    r = Routes()
    r.add("GET", "/plugins/operations/op1", replies=[
        dict(json=op("requested")), dict(json=op("delivered")), dict(json=op("downloading")),
        dict(json=op("downloading")), dict(json=op("verifying")), dict(json=op("installing")),
        dict(json=op("succeeded"))])
    code, lines, clock = await follow(r)
    assert code == 0
    text = "\n".join(lines)
    for stage in ("request received", "sent to the device", "downloading", "verifying", "installing"):
        assert stage in text
    assert text.count("downloading") == 1                         # a repeated status is not printed twice
    assert lines[-1].startswith("✓ installing camera done")
    assert clock.sleeps[0] == 0.5 and max(clock.sleeps) <= 3.0 and clock.sleeps == sorted(clock.sleeps)


@pytest.mark.asyncio
async def test_failure_shows_the_servers_message_and_hint_not_raw_text():
    r = Routes()
    r.add("GET", "/plugins/operations/op1", replies=[
        dict(json=op("installing")),
        dict(json=op("failed", error={"code": "sha_mismatch",
                                      "message": "The downloaded plugin package didn't match its checksum.",
                                      "hint": "Try again."}))])
    code, lines, _ = await follow(r)
    assert code == 1
    assert "✗ installing camera failed: The downloaded plugin package didn't match its checksum." in lines
    assert "  Try again." in lines


@pytest.mark.asyncio
async def test_timeout_is_reported_and_the_operation_keeps_running():
    r = Routes()
    r.add("GET", "/plugins/operations/op1", json_body=op("downloading"))
    code, lines, clock = await follow(r, timeout=10.0)
    assert code == 1 and clock.t >= 10.0
    assert any("still not finished after 10s" in l and "keeps going" in l for l in lines)
    assert any("operations --active" in l for l in lines)


@pytest.mark.asyncio
async def test_unknown_operation_and_a_dead_backend_end_cleanly():
    r = Routes()                                                   # no route: 404
    code, lines, _ = await follow(r)
    assert code == 1 and "can't read the status" in lines[0]

    import httpx

    def dead(req):
        raise httpx.ConnectError("down")
    clock = Clock()
    lines = []
    code = await follow_operation(make_client(dead), "op1", label="x", sleep=clock.sleep, clock=clock.now,
                                  out=lines.append)
    assert code == 1 and "lost contact" in lines[0]


def install_args(**kw):
    base = dict(command="install", plugin_id="camera", version="1.0.0", force=True, no_wait=False, timeout=30.0,
                json=False, device=None, watch=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.asyncio
async def test_install_follows_the_operation_it_was_given(capsys):
    r = Routes()
    r.add("GET", "/plugins/registry-none", json_body={})
    r.add("GET", "/plugins/catalog", json_body={"dev-1": []})
    r.add("POST", "/plugins/camera/install", json_body={"operation_id": "op1", "success": True})
    r.add("GET", "/plugins/operations/op1", replies=[dict(json=op("installing")), dict(json=op("succeeded"))])
    code = await cli._cmd_install(install_args(), make_client(r), "dev-1")
    out = capsys.readouterr().out
    assert code == 0 and "Installing camera@1.0.0 on dev-1" in out and "✓ installing camera@1.0.0 done" in out
    assert r.bodies("POST", "/plugins/camera/install") == [{"device_id": "dev-1", "version": "1.0.0"}]
    assert r.calls[0].headers["x-pai-device-id"] == "dev-1"        # this device asking: local


@pytest.mark.asyncio
async def test_install_no_wait_only_prints_the_operation(capsys):
    r = Routes()
    r.add("GET", "/plugins/catalog", json_body={})
    r.add("POST", "/plugins/camera/install", json_body={"operation_id": "op1"})
    code = await cli._cmd_install(install_args(no_wait=True), make_client(r), "dev-1")
    out = capsys.readouterr().out
    assert code == 0 and "operation op1" in out
    assert r.count("GET", "/plugins/operations/op1") == 0           # it did not poll


@pytest.mark.asyncio
async def test_uninstall_follows_and_fails_with_a_clear_message(capsys):
    r = Routes()
    r.add("POST", "/plugins/camera/uninstall", json_body={"operation_id": "op2"})
    r.add("GET", "/plugins/operations/op2", json_body=op("failed", error={
        "code": "remote_install_disabled", "message": "Remote plugin install is turned off on this device",
        "hint": "Turn it on in the app."}))
    args = argparse.Namespace(command="uninstall", plugin_id="camera", no_wait=False, timeout=20.0, json=False,
                              device=None, watch=False)
    code = await cli._cmd_uninstall(args, make_client(r), "dev-1")
    out = capsys.readouterr().out
    assert code == 1 and "✗ removing camera failed: Remote plugin install is turned off" in out


def test_operations_never_poll_from_the_agent_module():
    """The CLI polls; the agent/transport never does (the app is pushed to)."""
    import pai_device.agent.agent as agent
    assert "get_operation" not in Path(agent.__file__).read_text()
