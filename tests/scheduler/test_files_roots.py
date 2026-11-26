"""files.* must work out of the box: roots come from the plugin's own manifest (metadata.allowedRoots).

Regression: nothing ever filled ScopeContext.filesystem_roots, so every files.* call was denied with
"No filesystem roots configured".
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from pai.common.enums.permission import Origin
from pai.plugins.manifest_install import load_manifest_file
from pai.security.authorization.context import ScopeContext
from support import DEV, USER

_M = load_manifest_file(Path(__file__).resolve().parents[2] / "plugin_registry" / "plugins" / "files"
                        / "1.0.0" / "linux" / "manifest.json")
MANIFEST = SimpleNamespace(get_capability=lambda c: next((x for x in _M.capabilities if x.id == c), None),
                           metadata=_M.metadata)


async def _authorize(security, params, *, scope=None, manifest=MANIFEST, capability="files.list"):
    return await security.authorize(
        user_id=USER, plugin_id="p1", capability=capability, device_id=DEV, plugin_manifest=manifest,
        parameters=params, scope=scope or ScopeContext(allowed_devices=[DEV]),
        origin=Origin.USER_DIRECT, approved=True)


async def test_a_path_inside_the_manifest_roots_is_allowed(security):
    d = await _authorize(security, {"path": str(Path.home() / "Documents" / "notes.txt")})
    assert d.effect.value == "allow", d.reason


async def test_a_path_outside_the_manifest_roots_is_denied(security):
    d = await _authorize(security, {"path": "/etc/passwd"})
    assert d.effect.value == "deny" and "outside allowed roots" in d.reason


async def test_dotdot_cannot_escape_a_root(security):
    d = await _authorize(security, {"path": str(Path.home() / "Documents" / ".." / ".ssh" / "id_rsa")})
    assert d.effect.value == "deny"


async def test_roots_supplied_by_trusted_server_code_win_over_the_manifest(security):
    scope = ScopeContext(allowed_devices=[DEV], filesystem_roots=["/srv/only-here"])
    assert (await _authorize(security, {"path": str(Path.home() / "Documents" / "a")}, scope=scope)).effect.value == "deny"
    assert (await _authorize(security, {"path": "/srv/only-here/a"}, scope=scope)).effect.value == "allow"


async def test_a_manifest_without_roots_still_fails_closed(security):
    bare = SimpleNamespace(get_capability=MANIFEST.get_capability, metadata={})
    d = await _authorize(security, {"path": str(Path.home() / "Documents" / "a")}, manifest=bare)
    assert d.effect.value == "deny" and "No filesystem roots configured" in d.reason


# ---- every files.* action is scoped (read/stat/delete/copy/move used to declare no permission at all)

HOME = Path.home()
INSIDE, OUTSIDE = str(HOME / "Documents" / "a.txt"), "/etc/passwd"


def test_every_files_capability_declares_the_filesystem_permission():
    assert all(c.permissions == ["filesystem"] for c in _M.capabilities), \
        [c.id for c in _M.capabilities if c.permissions != ["filesystem"]]


@pytest.mark.parametrize("cap", ["files.read", "files.stat", "files.delete"])
async def test_single_path_actions_are_contained(security, cap):
    assert (await _authorize(security, {"path": INSIDE}, capability=cap)).effect.value == "allow"
    d = await _authorize(security, {"path": OUTSIDE}, capability=cap)
    assert d.effect.value == "deny" and "outside allowed roots" in d.reason


@pytest.mark.parametrize("cap", ["files.copy", "files.move"])
async def test_copy_and_move_check_both_ends(security, cap):
    assert (await _authorize(security, {"source": INSIDE, "destination": INSIDE + ".bak"}, capability=cap)).effect.value == "allow"
    assert (await _authorize(security, {"source": OUTSIDE, "destination": INSIDE}, capability=cap)).effect.value == "deny"
    d = await _authorize(security, {"source": INSIDE, "destination": "/etc/cron.d/evil"}, capability=cap)   # exfil / plant
    assert d.effect.value == "deny" and "outside allowed roots" in d.reason


async def test_a_required_path_cannot_be_left_out(security):
    d = await _authorize(security, {}, capability="files.read")
    assert d.effect.value == "deny" and "requires a path" in d.reason


# ---- camera: approved capture must not be denied, but an outputPath is contained

_C = load_manifest_file(Path(__file__).resolve().parents[2] / "plugin_registry" / "plugins" / "camera"
                        / "1.0.0" / "linux" / "manifest.json")
CAMERA = SimpleNamespace(get_capability=lambda c: next((x for x in _C.capabilities if x.id == c), None),
                         metadata=_C.metadata)


async def test_approved_camera_capture_without_a_path_is_allowed(security):
    d = await _authorize(security, {}, capability="camera.capture", manifest=CAMERA)
    assert d.effect.value == "allow", d.reason


async def test_camera_output_path_is_contained(security):
    ok = await _authorize(security, {"outputPath": str(HOME / "Pictures" / "x.jpg")}, capability="camera.capture", manifest=CAMERA)
    bad = await _authorize(security, {"outputPath": "/etc/cron.d/x.jpg"}, capability="camera.capture", manifest=CAMERA)
    assert ok.effect.value == "allow" and bad.effect.value == "deny"
