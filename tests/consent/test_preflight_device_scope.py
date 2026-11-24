"""The plugin tester's PREFLIGHT must treat the device being tested as allowlisted.

Regression: preflight called authorize() with no scope, so the allowed-devices list was empty and every
device-scoped permission (screen, camera, input) was denied with "Device is not allowlisted" before the
tester could even ask for approval. The route has already verified the device is the user's own and
online, exactly as the /invoke route and the task runner do (both add the target device).
"""
from pathlib import Path

import pytest

from consent_kit import DEVICE, USER
from pai.plugins.manifest_install import load_manifest_file
from pai.security.consent.models import PreflightDecision

pytestmark = pytest.mark.asyncio

REGISTRY = Path(__file__).resolve().parents[2] / "plugin_registry" / "plugins"
SCREEN = load_manifest_file(REGISTRY / "screen" / "1.1.0" / "linux" / "manifest.json")
INPUT = load_manifest_file(REGISTRY / "input" / "1.0.0" / "linux" / "manifest.json")


async def preflight(sec, manifest, capability, device=DEVICE, admin=False):
    return await sec.preflight(
        user_id=USER, plugin_id="p1", plugin_manifest=manifest, capability=capability, device_id=device,
        parameters={}, session_id=f"tester:{USER}", is_admin=admin, device_online=True)


@pytest.mark.parametrize("manifest,capability", [
    (SCREEN, "screen.capture"), (INPUT, "keyboard.type"), (INPUT, "mouse.click"), (INPUT, "input.start"),
])
async def test_device_scoped_capabilities_reach_the_confirmation_step(sec, manifest, capability):
    r = await preflight(sec, manifest, capability)
    assert r.decision == PreflightDecision.CONFIRM, (capability, r.decision, r.reason)
    assert "allowlisted" not in r.reason


async def test_an_admin_is_allowed_without_a_prompt_but_never_around_the_scope(sec):
    r = await preflight(sec, SCREEN, "screen.capture", admin=True)
    assert r.decision == PreflightDecision.ALLOW, r.reason


async def test_only_the_device_being_tested_is_allowlisted(sec):
    """The scope covers exactly the device in the request; it is not a blanket allow."""
    seen = []
    original = sec.authorize

    async def spy(**kw):
        seen.append(kw.get("scope"))
        return await original(**kw)

    sec.authorize = spy
    await preflight(sec, SCREEN, "screen.capture", device="dev-x")
    assert seen and all(s is not None and s.allowed_devices == ["dev-x"] for s in seen)


async def test_a_capability_without_a_device_scope_is_unaffected(kit):
    r = await kit.sec.preflight(
        user_id=USER, plugin_id="p1", plugin_manifest=__import__("conftest").MANIFEST, capability="act.low",
        device_id=DEVICE, parameters={}, session_id="s", is_admin=False, device_online=True)
    assert r.decision == PreflightDecision.CONFIRM
