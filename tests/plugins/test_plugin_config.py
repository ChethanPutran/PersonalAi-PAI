"""Per-device plugin CONFIGURATION (manifest configSchema): validation, storage, enforcement."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pai.plugins import config_schema as cs
from pai.plugins.manager import PluginManager
from pai.plugins.packager import PluginPackager
from pai.plugins.store import LocalArtifactStore
from pai.security.scopes.filesystem import FilesystemScope
from pai.storage.models import DeviceModel
from pai.storage.repositories.device_plugin import DevicePluginRepository

SCHEMA = {"type": "object", "properties": {
    "allowedPaths": {"type": "array", "items": {"type": "string"}, "default": ["/data/pai"],
                     "x-pai": {"widget": "paths"}},
    "deniedPaths": {"type": "array", "items": {"type": "string"}, "x-pai": {"widget": "paths"}},
    "readOnly": {"type": "boolean", "default": False},
    "maxMb": {"type": "integer", "minimum": 1, "maximum": 10},
}}


# ── validation ──────────────────────────────────────────────────────────────

def test_paths_are_normalised_deduplicated_and_must_be_absolute():
    out = cs.validate(SCHEMA, {"allowedPaths": ["/storage/emulated/0/DCIM/", "/storage/emulated/0/DCIM", "~/Pictures"]})
    assert out["allowedPaths"] == ["/storage/emulated/0/DCIM", "~/Pictures"]
    for bad in (["relative/dir"], ["/a/../etc"], [""], ["/a\x00b"]):
        with pytest.raises(cs.ConfigError):
            cs.validate(SCHEMA, {"allowedPaths": bad})


def test_unknown_keys_wrong_types_and_ranges_are_rejected_with_a_reason_per_key():
    with pytest.raises(cs.ConfigError) as e:
        cs.validate(SCHEMA, {"nope": 1, "readOnly": "yes", "maxMb": 99})
    assert set(e.value.errors) == {"nope", "readOnly", "maxMb"}


def test_effective_overlays_saved_on_defaults_and_none_resets():
    assert cs.effective(SCHEMA, {"readOnly": True}) == {"allowedPaths": ["/data/pai"], "readOnly": True}
    assert cs.validate(SCHEMA, {"readOnly": None}) == {}


# ── enforcement: denied folders win even inside an allowed root ───────────────

def _req(path, roots, denied=()):
    return SimpleNamespace(parameters={"path": path}, resource=SimpleNamespace(id=None),
                           context={"filesystem_roots": roots, "filesystem_denied": list(denied)})


def test_denied_folder_is_refused_inside_an_allowed_root_and_siblings_still_work():
    roots, denied = ["/storage/emulated/0"], ["/storage/emulated/0/Android"]
    assert not FilesystemScope.evaluate(_req("/storage/emulated/0/Android/data/x", roots, denied), None).allowed
    assert not FilesystemScope.evaluate(_req("/storage/emulated/0/Android", roots, denied), None).allowed
    assert FilesystemScope.evaluate(_req("/storage/emulated/0/DCIM/a.jpg", roots, denied), None).allowed
    # a sibling that merely starts with the same letters is NOT the denied folder
    assert FilesystemScope.evaluate(_req("/storage/emulated/0/Androidx/a", roots, denied), None).allowed


# ── storage through the manager ──────────────────────────────────────────────

@pytest.fixture
def make_manager(tmp_path):
    async def make(session_factory) -> PluginManager:
        plugins, pkgs = tmp_path / "plugins", tmp_path / "pkgs"
        d = plugins / "files" / "1.0.0" / "linux"
        (d / "artifact").mkdir(parents=True, exist_ok=True)
        (d / "artifact" / "libf.so").write_bytes(b"x")
        (d / "manifest.json").write_text(json.dumps({
            "schemaVersion": 1, "id": "files", "name": "Files", "type": "native", "version": "1.0.0",
            "author": "PAI", "license": "MIT", "runtime": {"kind": "device", "nativeModule": "pai.files"},
            "platforms": {"linux": {"artifact": "linux/libf.so", "entrypoint": "create"}},
            "architectures": ["x86_64"], "deviceTypes": ["desktop"], "permissions": ["filesystem"],
            "configSchema": SCHEMA,
            "capabilities": [{"id": "files.read", "description": "x"}]}))
        pkgs.mkdir(exist_ok=True)
        pm = PluginManager(event_bus=None, plugin_registry_dir=plugins, artifact_store=LocalArtifactStore(pkgs),
                           packager=PluginPackager(pkgs), session_factory=session_factory)
        await pm.registry.initialize()
        pm._invalidate = lambda user_id=None: None
        pm._emit = lambda *a, **k: None
        async def _noop(*a, **k): return None
        pm._publish = _noop
        return pm
    return make


async def _device(session_factory, device="d1"):
    async with session_factory() as s:
        s.add(DeviceModel(id=device, user_id="alice"))
        await s.commit()


async def test_config_can_be_chosen_before_install_and_survives_installing(session_factory, make_manager):
    pm = await make_manager(session_factory)
    await _device(session_factory)
    state = await pm.set_device_config("d1", "files", {"allowedPaths": ["/home/me/Pictures"], "readOnly": True})
    assert state["effective"] == {"allowedPaths": ["/home/me/Pictures"], "readOnly": True}
    async with session_factory() as s:
        await DevicePluginRepository(s).mark_installed("d1", "files", "1.0.0")
        await s.commit()
    assert (await pm.get_device_config("d1", "files"))["values"]["readOnly"] is True


async def test_set_merges_resets_and_rejects_bad_values_without_changing_anything(session_factory, make_manager):
    pm = await make_manager(session_factory)
    await _device(session_factory)
    await pm.set_device_config("d1", "files", {"readOnly": True, "deniedPaths": ["/home/me/.ssh"]})
    state = await pm.set_device_config("d1", "files", {"readOnly": None})
    assert state["values"] == {"deniedPaths": ["/home/me/.ssh"]}
    with pytest.raises(cs.ConfigError):
        await pm.set_device_config("d1", "files", {"allowedPaths": ["relative"]})
    assert (await pm.get_device_config("d1", "files"))["values"] == {"deniedPaths": ["/home/me/.ssh"]}
    with pytest.raises(ValueError):
        await pm.set_device_config("d1", "ghost", {"x": 1})
