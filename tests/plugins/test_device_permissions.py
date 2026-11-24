"""Device-reported permissions: what the user allowed ON THE DEVICE for each installed plugin.

Advisory (the device re-checks before every invoke): stored per device plugin, used so the router stops
offering capabilities the device would refuse and can say why. Unreported = unknown = blocks nothing.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from pai.orchestration.capability_router import (CapabilityNotAvailable, CapabilityRouter, _DeviceInstall,
                                                 not_allowed_on_device)
from pai.plugins.manager import PluginManager
from pai.plugins.manifest import PluginManifest
from pai.plugins.packager import PluginPackager
from pai.plugins.registry import PluginRegistry
from pai.plugins.store import LocalArtifactStore
from pai.storage.models import DeviceModel
from pai.storage.repositories.device_plugin import DevicePluginRepository

DEVICE, OTHER_DEVICE, USER = "dev-1", "dev-2", "alice"


def manifest_dict(perms=("screen",)) -> dict:
    return {
        "schemaVersion": 1, "id": "shot", "name": "Shot", "type": "native", "version": "1.0.0",
        "author": "PAI", "license": "MIT",
        "runtime": {"kind": "device", "nativeModule": "pai.shot"},
        "platforms": {"linux": {"artifact": "linux/libshot.so", "entrypoint": "create"}},
        "architectures": ["x86_64"], "deviceTypes": ["desktop"], "permissions": list(perms),
        "capabilities": [{"id": "shot.take", "description": "x"}],
    }


@pytest.fixture
def manager_factory(tmp_path):
    """Async factory: a PluginManager over a real plugin dir (declares permission 'screen') + the in-memory DB."""
    async def make(session_factory, perms=("screen",)) -> PluginManager:
        plugins, pkgs = tmp_path / "plugins", tmp_path / "pkgs"
        d = plugins / "shot" / "1.0.0" / "linux"
        (d / "artifact").mkdir(parents=True, exist_ok=True)
        (d / "artifact" / "libshot.so").write_bytes(b"x")
        (d / "manifest.json").write_text(json.dumps(manifest_dict(perms)))
        pkgs.mkdir(exist_ok=True)
        pm = PluginManager(event_bus=None, plugin_registry_dir=plugins, artifact_store=LocalArtifactStore(pkgs),
                           packager=PluginPackager(pkgs), session_factory=session_factory)
        await pm.registry.initialize()
        pm._invalidations = []
        pm._invalidate = lambda user_id=None: pm._invalidations.append(user_id)
        pm._emit = lambda *a, **k: None
        return pm
    return make


async def install(session_factory, device=DEVICE, plugin="shot", version="1.0.0"):
    async with session_factory() as s:
        if await s.get(DeviceModel, device) is None:
            s.add(DeviceModel(id=device, user_id=USER))
        repo = DevicePluginRepository(s)
        await repo.mark_installed(device, plugin, version)
        await repo.set_device_enabled(device, plugin, True)
        await s.commit()


# ── repository ──────────────────────────────────────────────────────────────

async def test_repository_stores_only_for_an_installed_plugin_and_only_valid_states(session_factory):
    await install(session_factory)
    async with session_factory() as s:
        repo = DevicePluginRepository(s)
        assert await repo.set_permissions(DEVICE, "shot", {"screen": "granted", "x": "maybe"})
        assert (await repo.get(DEVICE, "shot")).permissions == {"screen": "granted"}        # invalid state dropped
        assert (await repo.get(DEVICE, "shot")).permissions_reported_at is not None
        assert not await repo.set_permissions(DEVICE, "nope", {"screen": "granted"})        # a report cannot create rows
        assert not await repo.set_permissions("ghost-device", "shot", {"screen": "granted"})


async def test_uninstalling_clears_the_reported_permissions(session_factory):
    await install(session_factory)
    async with session_factory() as s:
        repo = DevicePluginRepository(s)
        await repo.set_permissions(DEVICE, "shot", {"screen": "denied"})
        await repo.mark_uninstalled(DEVICE, "shot")
        assert (await repo.get(DEVICE, "shot")).permissions is None
        assert not await repo.set_permissions(DEVICE, "shot", {"screen": "denied"})          # no longer installed


async def test_reconcile_removing_a_plugin_clears_its_permissions(session_factory):
    await install(session_factory)
    async with session_factory() as s:
        repo = DevicePluginRepository(s)
        await repo.set_permissions(DEVICE, "shot", {"screen": "denied"})
        await repo.reconcile(DEVICE, [])                       # the device no longer has it
        assert (await repo.get(DEVICE, "shot")).permissions is None


# ── manager: what a report may contain ──────────────────────────────────────

async def test_report_keeps_only_declared_permissions_with_valid_states(session_factory, manager_factory):
    await install(session_factory)
    pm = await manager_factory(session_factory)
    out = await pm.report_device_permissions(device_id=DEVICE, plugins={
        "shot": {"screen": "denied", "camera": "denied", "screen2": "granted", "x": "junk"}})
    assert out == {"updated": ["shot"], "ignored": []}
    rows = {r["plugin_id"]: r for r in await pm.list_device_plugins(DEVICE)}
    assert rows["shot"]["permissions"] == {"screen": "denied"}          # a device cannot invent permissions
    assert rows["shot"]["permissions_reported_at"] is not None
    assert pm._invalidations                                           # the router's capability index is rebuilt


async def test_report_ignores_unknown_uninstalled_and_empty_entries_without_failing(session_factory, manager_factory):
    await install(session_factory)
    pm = await manager_factory(session_factory)
    out = await pm.report_device_permissions(device_id=DEVICE, plugins={
        "shot": {"screen": "granted"}, "unknown-plugin": {"screen": "granted"}, "also-unknown": {}})
    assert out["updated"] == ["shot"] and sorted(out["ignored"]) == ["also-unknown", "unknown-plugin"]
    out = await pm.report_device_permissions(device_id=DEVICE, plugins={"shot": {"screen": "bogus-state"}})
    assert out == {"updated": [], "ignored": ["shot"]}                 # nothing valid in it: left as it was


async def test_a_plugin_installed_on_another_device_is_not_touched(session_factory, manager_factory):
    await install(session_factory, DEVICE)
    pm = await manager_factory(session_factory)
    out = await pm.report_device_permissions(device_id=OTHER_DEVICE, plugins={"shot": {"screen": "denied"}})
    assert out["updated"] == []
    rows = {r["plugin_id"]: r for r in await pm.list_device_plugins(DEVICE)}
    assert rows["shot"]["permissions"] == {}


# ── router: capabilities follow what the user allowed on the device ─────────

async def router_with(installs, tmp_path, devices=("dev-1",)):
    """A CapabilityRouter over a real registry whose per-device installs are given."""
    import asyncio
    from pai.devices.models import DeviceStatus

    plugins, pkgs = tmp_path / "plugins", tmp_path / "pkgs"
    d = plugins / "shot" / "1.0.0" / "linux"
    (d / "artifact").mkdir(parents=True)
    pkgs.mkdir()
    (d / "artifact" / "libshot.so").write_bytes(b"x")
    (d / "manifest.json").write_text(json.dumps(manifest_dict()))
    reg = PluginRegistry(plugins, LocalArtifactStore(pkgs), PluginPackager(pkgs))
    await reg.initialize()

    class PM:
        registry = reg
        def get_catalog(self): return reg.get_catalog()
        def get_manifest(self, pid, v=None): return reg.get_manifest(pid, v)

    class Dev:
        def __init__(self, i):
            self.id = self.name = i
            self.status = DeviceStatus.ONLINE
            self.device_type, self.platform, self.architecture, self.app_version = "desktop", "linux", "x86_64", "1.0.0"

    class DM:
        async def list_devices(self, user_id): return [Dev(i) for i in devices]

    r = CapabilityRouter.__new__(CapabilityRouter)
    r.plugin_manager, r.device_manager, r.builtin_registry, r._session_factory = PM(), DM(), None, object()
    r._cache, r._cache_lock = {}, asyncio.Lock()

    async def enabled(user_id): return {"shot"}
    async def state(ids): return {i: installs.get(i, {}) for i in ids}
    r._user_enabled_plugin_ids, r._device_enabled_plugin_ids = enabled, state
    r._executor_for = staticmethod(lambda plugin: "device_executor")
    r._score = staticmethod(lambda d, p: 1.0)
    return r


def install_of(state: str | None) -> _DeviceInstall:
    return _DeviceInstall("1.0.0", {} if state is None else {"screen": state})


@pytest.mark.parametrize("state,offered", [("granted", True), (None, True), ("denied", False), ("pending", False)])
async def test_capabilities_are_offered_only_if_the_device_says_allowed_or_says_nothing(state, offered, tmp_path):
    index = await (await router_with({"dev-1": {"shot": install_of(state)}}, tmp_path))._build_index("u")
    assert ("shot.take" in index) is offered


async def test_one_allowed_device_is_enough_and_only_that_device_is_a_target(tmp_path):
    r = await router_with({"dev-1": {"shot": install_of("denied")}, "dev-2": {"shot": install_of("granted")}},
                    tmp_path, devices=("dev-1", "dev-2"))
    index = await r._build_index("u")
    assert [t.device_id for t in index["shot.take"]] == ["dev-2"]


async def test_the_reason_is_explained_when_the_capability_is_asked_for(tmp_path):
    r = await router_with({"dev-1": {"shot": install_of("pending")}}, tmp_path)
    with pytest.raises(CapabilityNotAvailable) as e:
        await r.resolve(capability="shot.take", user_id="u")
    assert e.value.error.detail["stage"] == "permission_not_allowed_on_device"
    assert e.value.error.detail["permissions"] == ["screen"]
    assert "'screen'" in e.value.error.reason and "Allow it in the PAI app" in e.value.error.reason


async def test_diagnosis_names_only_the_devices_that_have_it_but_not_allowed(tmp_path):
    r = await router_with({"dev-1": {"shot": install_of("denied")}}, tmp_path, devices=("dev-1", "dev-2"))   # dev-2 lacks the plugin
    with pytest.raises(CapabilityNotAvailable) as e:
        await r.resolve(capability="shot.take", user_id="u")
    assert e.value.error.detail["stage"] == "permission_not_allowed_on_device"
    assert e.value.error.detail["devices"] == ["dev-1"]


def test_not_allowed_helper_ignores_permissions_the_plugin_does_not_declare():
    m = PluginManifest.model_validate(manifest_dict(("screen",)))
    assert not_allowed_on_device(m, {"camera": "denied"}) == []
    assert not_allowed_on_device(m, {"screen": "granted"}) == []
    assert not_allowed_on_device(m, {"screen": "denied", "camera": "denied"}) == ["screen"]


# ── route ───────────────────────────────────────────────────────────────────

async def route_app(session_factory, manager_factory, owner=USER):
    from pai.api.routes import plugins as plugins_routes

    pm = await manager_factory(session_factory)

    class DM:
        async def get(self, device_id):
            return SimpleNamespace(id=device_id, user_id=owner) if device_id == DEVICE else None

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User")
        return await call_next(request)

    app.include_router(plugins_routes.router, prefix="/api/v1")
    app.state.context = SimpleNamespace(orchestrator=SimpleNamespace(plugin_manager=pm, device_manager=DM()))
    return app, pm


def H(user=USER):
    return {"X-User": user}


async def test_route_stores_the_owners_report(session_factory, manager_factory):
    await install(session_factory)
    app, pm = await route_app(session_factory, manager_factory)
    with TestClient(app) as c:
        r = c.post(f"/api/v1/plugins/device/{DEVICE}/permissions", json={"plugins": {"shot": {"screen": "denied"}}}, headers=H())
        assert r.status_code == 200 and r.json()["updated"] == ["shot"]
    rows = {x["plugin_id"]: x for x in await pm.list_device_plugins(DEVICE)}
    assert rows["shot"]["permissions"] == {"screen": "denied"}


async def test_route_refuses_someone_elses_device_and_anonymous_callers(session_factory, manager_factory):
    await install(session_factory)
    app, pm = await route_app(session_factory, manager_factory)
    body = {"plugins": {"shot": {"screen": "denied"}}}
    with TestClient(app) as c:
        assert c.post(f"/api/v1/plugins/device/{DEVICE}/permissions", json=body, headers=H("mallory")).status_code == 404
        assert c.post("/api/v1/plugins/device/ghost/permissions", json=body, headers=H()).status_code == 404
        assert c.post(f"/api/v1/plugins/device/{DEVICE}/permissions", json=body).status_code == 401
    rows = {x["plugin_id"]: x for x in await pm.list_device_plugins(DEVICE)}
    assert rows["shot"]["permissions"] == {}                        # mallory hid nothing


async def test_route_validates_the_shape_and_size_of_a_report(session_factory, manager_factory):
    await install(session_factory)
    app, _ = await route_app(session_factory, manager_factory)
    url = f"/api/v1/plugins/device/{DEVICE}/permissions"
    with TestClient(app) as c:
        assert c.post(url, json={"plugins": {"shot": {"screen": 5}}}, headers=H()).status_code == 422
        assert c.post(url, json={"plugins": "nope"}, headers=H()).status_code == 422
        assert c.post(url, json={"plugins": {f"p{i}": {} for i in range(129)}}, headers=H()).status_code == 422
        assert c.post(url, json={"plugins": {"shot": {f"perm{i}": "granted" for i in range(33)}}}, headers=H()).status_code == 422
        assert c.post(url, json={}, headers=H()).status_code == 200                       # an empty report is harmless
