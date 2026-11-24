"""Plugin versioning: multi-version registry, compatibility gates, update info, and
version-accurate capabilities (a device on v1 must not be offered v2's capabilities)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pai.orchestration.capability_router import CapabilityRouter
from pai.plugins.manifest_install import load_manifest_file
from pai.plugins.packager import PluginPackager
from pai.plugins.registry import PluginRegistry
from pai.plugins.store import LocalArtifactStore
from pai.plugins.updates import build_update_info, capability_diff, version_summary
from pai.version import RUNTIME_VERSION, check_compatibility


def _manifest(version: str, caps: list[str], *, min_pai=None, min_app=None, changelog=None, schema=1) -> dict:
    runtime = {"kind": "device", "nativeModule": "pai.camera"}
    if min_pai:
        runtime["minPaiVersion"] = min_pai
    if min_app:
        runtime["minAppVersion"] = min_app
    m = {
        "schemaVersion": schema, "id": "camera_linux", "name": "Camera", "type": "native",
        "version": version, "description": "cam", "author": "PAI", "license": "MIT",
        "runtime": runtime,
        "platforms": {"linux": {"artifact": "linux/libcam.so", "entrypoint": "create"}},
        "architectures": ["x86_64"], "deviceTypes": ["desktop"], "permissions": ["camera"],
        "capabilities": [{"id": c, "description": c, "parameters": {"type": "object", "properties": {}}} for c in caps],
    }
    if changelog:
        m["changelog"] = changelog
    return m


def _write(root: Path, manifest: dict) -> None:
    d = root / "camera" / manifest["version"] / "linux"
    d.mkdir(parents=True)
    (d / "artifact").mkdir()                       # only <platform>/artifact/ is packaged
    (d / "artifact" / "libcam.so").write_bytes(b"\x7fELF" + manifest["version"].encode())
    (d / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
async def registry(tmp_path):
    plugins, pkgs = tmp_path / "plugins", tmp_path / "pkgs"
    plugins.mkdir(); pkgs.mkdir()
    _write(plugins, _manifest("1.0.0", ["camera.capture", "camera.record"]))
    _write(plugins, _manifest(
        "1.1.0", ["camera.capture", "camera.record"],
        changelog={"summary": "fixes", "fixes": ["fix blur"]}))
    _write(plugins, _manifest(
        "2.0.0", ["camera.capture", "camera.record", "camera.stream"], min_app="1.4.0",
        changelog={"summary": "streaming", "features": ["Live streaming"], "breaking": ["format"]}))
    _write(plugins, _manifest("3.0.0", ["camera.capture"], min_pai="99.0.0"))   # server too old
    reg = PluginRegistry(plugins, LocalArtifactStore(pkgs), PluginPackager(pkgs))
    await reg.initialize()
    return reg


# ── version compatibility primitives ────────────────────────────────────────

def test_check_compatibility():
    ok = check_compatibility(schema_version=1, min_runtime="0.4.0", min_app="1.0.0", app_version="1.0.0")
    assert ok.compatible
    assert check_compatibility(schema_version=1, min_runtime="99.0.0", min_app=None).code == "runtime_too_old"
    assert check_compatibility(schema_version=1, min_runtime=None, min_app="2.0.0", app_version="1.9.9").code == "app_too_old"
    assert check_compatibility(schema_version=9, min_runtime=None, min_app=None).code == "schema_unsupported"
    # unknown client app: not locked out (the client re-checks locally)
    assert check_compatibility(schema_version=1, min_runtime=None, min_app="2.0.0", app_version=None).compatible


def test_manifest_validation_rejects_bad_versions(tmp_path):
    for bad in (_manifest("1.0.0", ["a.b"], min_app="not-a-version"), _manifest("banana", ["a.b"]),
                _manifest("1.0.0", ["a.b"], schema=7)):
        p = tmp_path / "m.json"
        p.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            load_manifest_file(p)


# ── registry ────────────────────────────────────────────────────────────────

async def test_registry_keeps_all_versions_and_picks_newest_usable(registry):
    assert [i.version for i in registry.list_versions("camera_linux")] == ["3.0.0", "2.0.0", "1.1.0", "1.0.0"]
    # 3.0.0 needs a newer server -> default is 2.0.0 and 3.0.0's caps are not advertised
    assert registry.get_info("camera_linux").version == "2.0.0"
    assert registry.get_manifest("camera_linux", "1.0.0").capability_names == ["camera.capture", "camera.record"]
    assert registry.get_manifest("camera_linux", "9.9.9") is None
    assert registry.has_plugin("camera_linux", "1.1.0")


async def test_catalog_respects_device_app_version(registry):
    old_app = await registry.get_compatible_plugins("linux", "x86_64", "1.0.0")
    assert [p.version for p in old_app] == ["1.1.0"]          # 2.0.0 needs app 1.4.0
    new_app = await registry.get_compatible_plugins("linux", "x86_64", "1.4.0")
    assert [p.version for p in new_app] == ["2.0.0"]


# ── update info ─────────────────────────────────────────────────────────────

async def test_update_available_with_changelog_and_capability_diff(registry):
    u = build_update_info(registry, "camera_linux", "1.0.0", app_version="1.4.0")
    assert u["update_available"] and u["latest_version"] == "2.0.0"
    assert u["capabilities"]["added"] == ["camera.stream"]
    # release notes of every skipped version, oldest first
    assert [c["version"] for c in u["changelog"]] == ["1.1.0", "2.0.0"]
    assert u["changelog"][1]["features"] == ["Live streaming"]
    assert u["blocked"]["version"] == "3.0.0" and u["blocked"]["code"] == "runtime_too_old"


async def test_update_blocked_by_old_app_offers_newest_runnable(registry):
    u = build_update_info(registry, "camera_linux", "1.0.0", app_version="1.0.0")
    assert u["latest_version"] == "1.1.0" and u["update_available"]
    assert u["capabilities"]["added"] == []
    assert u["blocked"]["version"] == "3.0.0"   # newest overall is still reported


async def test_no_update_when_current(registry):
    u = build_update_info(registry, "camera_linux", "2.0.0", app_version="1.4.0")
    assert not u["update_available"]


async def test_version_summary_flags_compat(registry):
    by_v = {v["version"]: v for v in version_summary(registry, "camera_linux", "1.0.0")}
    assert by_v["1.0.0"]["compat_compatible"] is True
    assert by_v["2.0.0"]["compat_code"] == "app_too_old"
    assert by_v["3.0.0"]["compat_code"] == "runtime_too_old"


def test_capability_diff_detects_removed():
    old = load_manifest_obj(_manifest("1.0.0", ["a.x", "a.y"]))
    new = load_manifest_obj(_manifest("2.0.0", ["a.y", "a.z"]))
    assert capability_diff(old, new) == {"added": ["a.z"], "removed": ["a.x"], "changed": []}


def load_manifest_obj(raw):
    from pai.plugins.manifest import PluginManifest
    return PluginManifest.model_validate(raw)


# ── router: capabilities follow the INSTALLED version ───────────────────────

class _Dev:
    def __init__(self, i, app_version="1.4.0"):
        self.id, self.status, self.device_type = i, "online", "desktop"
        self.platform, self.architecture, self.app_version = "linux", "x86_64", app_version
        self.name = i


async def test_router_offers_only_installed_version_capabilities(registry):
    from pai.devices.models import DeviceStatus

    reg = registry

    class PM:
        registry = reg
        def get_catalog(self): return reg.get_catalog()
        def get_manifest(self, pid, v=None): return reg.get_manifest(pid, v)

    class DM:
        async def list_devices(self, user_id):
            a, b = _Dev("old"), _Dev("new")
            a.status = b.status = DeviceStatus.ONLINE
            return [a, b]

    router = CapabilityRouter.__new__(CapabilityRouter)
    router.plugin_manager, router.device_manager = PM(), DM()
    router.builtin_registry, router._session_factory = None, object()

    async def enabled(user_id): return {"camera_linux"}
    async def state(ids):
        from pai.orchestration.capability_router import _DeviceInstall
        return {"old": {"camera_linux": _DeviceInstall("1.0.0")}, "new": {"camera_linux": _DeviceInstall("2.0.0")}}
    router._user_enabled_plugin_ids, router._device_enabled_plugin_ids = enabled, state
    router._executor_for = staticmethod(lambda plugin: "device_executor")
    router._score = staticmethod(lambda d, p: 1.0)

    index = await router._build_index("u")
    streams = index["camera.stream"]
    assert [t.device_id for t in streams] == ["new"]            # v1 device never offered streaming
    assert {t.device_id for t in index["camera.capture"]} == {"old", "new"}
    assert {t.plugin_version for t in index["camera.capture"]} == {"1.0.0", "2.0.0"}
