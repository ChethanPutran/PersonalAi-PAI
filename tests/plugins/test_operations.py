"""Remote install operations: forward-only status, push to the owner's devices, failure text, watchdog."""
import asyncio
from types import SimpleNamespace

import pytest

from pai.plugins.operations import PluginOperations, describe_failure
from pai.session.manager import SessionManager


class FakeDevices:
    def __init__(self, user_devices, connected):
        self._devs = [SimpleNamespace(id=d) for d in user_devices]
        self.connected = set(connected)
        self.sent = []

    async def list_devices(self, user_id):
        return self._devs if user_id == "u1" else []

    async def is_connected(self, device_id):
        return device_id in self.connected

    async def send(self, device_id, frame):
        self.sent.append((device_id, frame))


@pytest.fixture
def make(session_factory):
    made = []

    def _make(devices):
        ops = PluginOperations(sessions=SessionManager(session_factory), device_manager=devices)
        ops.TICK_SECONDS = 0.02
        made.append(ops)
        return ops
    yield _make


@pytest.mark.asyncio
async def test_progress_moves_forward_is_pushed_to_every_connected_device_and_finishes_once(make):
    devices = FakeDevices(["A", "B"], connected=["A", "B"])
    ops = make(devices)
    op = await ops.create(user_id="u1", device_id="B", plugin_id="camera", version="1.0.0",
                          kind="install", requested_by_device="A")
    assert op["status"] == "requested" and not op["terminal"]
    assert {d for d, _ in devices.sent} == {"A", "B"}            # owner's devices are told, no polling
    assert devices.sent[0][1]["type"] == "plugin.operation"

    for status in ("delivered", "downloading", "verifying", "installing"):
        assert (await ops.progress(op["id"], device_id="B", status=status))["status"] == status
    assert await ops.progress(op["id"], device_id="B", status="delivered") is None   # never backwards

    done = await ops.progress(op["id"], device_id="B", status="succeeded", version="1.0.0")
    assert done["terminal"] and done["status"] == "succeeded" and done["error"] is None
    assert await ops.progress(op["id"], device_id="B", status="failed", error="boom") is None  # final
    await ops.stop()


@pytest.mark.asyncio
async def test_only_the_target_device_can_report_and_only_the_owner_can_read(make):
    ops = make(FakeDevices(["B"], connected=["B"]))
    op = await ops.create(user_id="u1", device_id="B", plugin_id="camera", version="1", kind="install")
    assert await ops.progress(op["id"], device_id="OTHER", status="succeeded") is None
    assert (await ops.get(op["id"], user_id="u1"))["status"] == "requested"
    assert await ops.get(op["id"], user_id="someone-else") is None
    assert await ops.list(user_id="someone-else") == []
    await ops.stop()


@pytest.mark.asyncio
async def test_failure_shows_plain_words_a_code_and_a_hint_never_the_raw_text(make):
    ops = make(FakeDevices(["B"], connected=["B"]))
    op = await ops.create(user_id="u1", device_id="B", plugin_id="camera", version="1", kind="install")
    failed = await ops.progress(op["id"], device_id="B", status="failed",
                                error="SHA256 mismatch: expected sha256:aa, got bb")
    err = failed["error"]
    assert err["code"] == "sha_mismatch" and "checksum" in err["message"] and err["hint"]
    assert "expected" not in str(failed)                          # raw device text stays server-side
    await ops.stop()


@pytest.mark.asyncio
async def test_newer_request_supersedes_an_open_one_for_the_same_target(make):
    ops = make(FakeDevices(["B"], connected=["B"]))
    first = await ops.create(user_id="u1", device_id="B", plugin_id="camera", version="1", kind="install")
    await ops.create(user_id="u1", device_id="B", plugin_id="camera", version="2", kind="install")
    old = await ops.get(first["id"], user_id="u1")
    assert old["status"] == "failed" and old["error"]["code"] == "superseded"
    await ops.stop()


@pytest.mark.asyncio
async def test_watchdog_fails_an_operation_whose_device_went_offline(make):
    devices = FakeDevices(["B"], connected=[])                    # never connected
    ops = make(devices)
    ops.OFFLINE_GRACE_SECONDS = 0.05
    op = await ops.create(user_id="u1", device_id="B", plugin_id="camera", version="1", kind="install")
    for _ in range(100):
        cur = await ops.get(op["id"], user_id="u1")
        if cur["terminal"]:
            break
        await asyncio.sleep(0.02)
    assert cur["status"] == "failed" and cur["error"]["code"] == "device_offline"
    await ops.stop()


@pytest.mark.asyncio
async def test_watchdog_fails_a_silent_device_and_leaves_no_task_behind(make):
    devices = FakeDevices(["B"], connected=["B"])
    ops = make(devices)
    ops.STALL_SECONDS = 0.1
    op = await ops.create(user_id="u1", device_id="B", plugin_id="camera", version="1", kind="install")
    for _ in range(150):
        cur = await ops.get(op["id"], user_id="u1")
        if cur["terminal"]:
            break
        await asyncio.sleep(0.02)
    assert cur["error"]["code"] == "device_unresponsive"
    await asyncio.sleep(0.05)
    assert not ops._watchers                                      # no leaked background task
    await ops.stop()


@pytest.mark.asyncio
async def test_operations_left_by_a_previous_run_are_closed_on_start(make):
    ops = make(FakeDevices(["B"], connected=["B"]))
    op = await ops.create(user_id="u1", device_id="B", plugin_id="camera", version="1", kind="install")
    await ops.stop()                                              # "the process died"
    fresh = make(FakeDevices(["B"], connected=["B"]))
    await fresh.start()
    cur = await fresh.get(op["id"], user_id="u1")
    assert cur["status"] == "failed" and cur["error"]["code"] == "server_restarted"


def test_describe_failure_has_a_generic_fallback():
    code, message, hint = describe_failure("something odd")
    assert code == "install_failed" and message and hint
