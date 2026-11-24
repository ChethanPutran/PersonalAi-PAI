from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from pai.common.enums.device import DevicePlatform
from pai.core.lifecycle import Lifecycle
from pai.observability.tracer import get_tracer
from pai.plugins.manifest import PluginManifest
from pai.plugins.manifest_info import PluginInfo
from pai.plugins.manifest_install import load_manifest_file
from pai.plugins.packager import PackageArtifact, PluginPackager
from pai.plugins.store import PluginArtifactStore
from packaging.version import InvalidVersion, Version

from pai.version import RUNTIME_VERSION, Compatibility, check_compatibility, version_key
from dataclasses import dataclass


MANIFEST_FILENAME = "manifest.json"

@dataclass(frozen=True)
class _Entry:
    info: PluginInfo
    manifest: PluginManifest
    path: Path


class PluginRegistry(Lifecycle):
    name = "plugin_registry"
    """
        Discovers plugin manifests and stores in the plugin store.
        EVERY version found on disk is kept (`_versions`); the flat `_info`/`_manifests`/`_paths`
        maps and the capability registry point at the newest version this runtime can host.
        Older versions stay addressable because a device may still have them installed, and
        what that device can do is decided by the version it actually has. The registry is
        responsible for:
        - Discovering plugins in the plugins directory
        - Loading and validating plugin manifests
        - Registering plugin metadata in the registry
        - Resolving capabilities to plugins
        """
    def __init__(self, 
                 plugins_dir: Path,
                 artifact_store: PluginArtifactStore,
                 packager: PluginPackager
                 ) -> None:
        if isinstance(plugins_dir, str):
            plugins_dir = Path(plugins_dir)
        if not plugins_dir.exists():
            raise FileNotFoundError(f"Plugin directory does not exist: {plugins_dir}")
        if not plugins_dir.is_dir():
            raise NotADirectoryError(f"Plugin directory is not a directory: {plugins_dir}")
        self.plugins_dir = plugins_dir
        self.packager =  packager 
        self.artifact_store = artifact_store
        self._info: Dict[str, PluginInfo] = {}
        self._manifests: Dict[str, PluginManifest] = {} 
        self._paths: Dict[str, Path] = {}       # id -> plugin dir
        # plugin id -> version -> entry (all versions on disk)
        self._versions: Dict[str, Dict[str, _Entry]] = {}
        # capability -> plugin ID -> PluginInfo
        self._capability_registry: Dict[str, Dict[str, PluginInfo]] = {}
        self._initialized = False

            
    # ── lifecycle ───────────────────────────────────────────────
    def is_initialized(self) -> bool:
        return self._initialized
    
    async def initialize(self) -> None:
        """Discover plugins and register them."""
        tracer = get_tracer()
        with tracer.span("plugin_registry.initialize"):
            await self.discover()
            self._initialized = True

    async def shutdown(self) -> None:
        """Clear all plugin registrations."""
        self._manifests.clear()
        self._capability_registry.clear()
        self._info.clear()
        self._paths.clear()
        self._versions.clear()
        self._initialized = False
        logger.info("PluginRegistry: shutdown complete")

    # ── discovery ───────────────────────────────────────────────
    async def discover(self) -> None:
        """Discover all plugins under the plugins directory."""
        tracer = get_tracer()
        logger.info("Discovering plugins in '{}'", self.plugins_dir)
        async with tracer.span("plugin_registry.discover") as span:
            self._info.clear()
            self._manifests.clear()
            self._paths.clear()
            self._versions.clear()
            self._capability_registry.clear()

            if not self.plugins_dir.exists():
                span.set_error(f"directory_not_found: {self.plugins_dir}")
                raise FileNotFoundError(
                    f"Plugin directory does not exist: {self.plugins_dir}"
                )

            discovered = 0
            skipped_non_server = 0
            failures: List[Dict[str, str]] = []

            for plugin_dir in sorted(self.plugins_dir.iterdir()):
                try:
                    with tracer.span("plugin_registry.ingest_plugin", plugin_dir=plugin_dir.name):
                        if not plugin_dir.is_dir() or plugin_dir.name.startswith("."):
                            continue
                        self._ingest(plugin_dir)
                except Exception as exc:
                    logger.warning("failed to ingest {}: {}", plugin_dir.name, exc)
                    failures.append({"plugin": plugin_dir.name, "error": str(exc)})

            self._rebuild_latest()
            span.set_attribute("registered", len(self._info))
            span.set_attribute("failures", failures)
            span.set_attribute("capability_registry_size",
                len(self._capability_registry),
            )

            logger.info(
                "Plugin discovery complete: {} registered, {} skipped (non-server), "
                "{} failed, {} capabilities",
                discovered, skipped_non_server,
                len(failures), len(self._capability_registry),
            )

    def _ingest(self, plugin_dir: Path) -> None:
        for version_dir in sorted(plugin_dir.iterdir()):
            if not version_dir.is_dir() or version_dir.name.startswith("."):
                continue

            for platform_dir in sorted(version_dir.iterdir()):
                if not platform_dir.is_dir() or platform_dir.name.startswith("."):
                    continue

                manifest_path = platform_dir / MANIFEST_FILENAME

                if not manifest_path.is_file():
                    continue

                try:
                    manifest = load_manifest_file(manifest_path)
                    platform = DevicePlatform(platform_dir.name)
                    artifact = self.packager.package(
                        manifest=manifest,
                        version_dir=version_dir,
                        platform=platform,
                    )
                    self._register(manifest, version_dir, artifact)
                except Exception as exc:
                    # One bad version must not hide the plugin's other versions.
                    logger.warning(
                        "skipping {}/{}/{}: {}",
                        plugin_dir.name, version_dir.name, platform_dir.name, exc,
                    )


    async def get_compatible_plugins(
        self, platform: str, architecture: str, app_version: Optional[str] = None,
    ) -> List[PluginInfo]:
        """One entry per plugin for this platform: the newest version this runtime AND the
        given app can run. A plugin with no runnable version is still listed, flagged
        ``compatible=False`` with the reason, so the UI can explain instead of hiding it."""
        if not self._initialized:
            await self.initialize()
        out: List[PluginInfo] = []
        for plugin_id, versions in self._versions.items():
            ordered = sorted(versions.values(), key=lambda e: version_key(e.info.version), reverse=True)
            ordered = [e for e in ordered if platform in e.info.platforms and architecture in e.info.architectures]
            if not ordered:
                continue
            for e in ordered:
                if self.entry_compatibility(e.manifest, app_version).compatible:
                    out.append(e.info)
                    break
            else:
                reason = self.entry_compatibility(ordered[0].manifest, app_version).reason
                out.append(ordered[0].info.model_copy(
                    update={"compatible": False, "incompatibleReason": reason}))
        if not out:
            logger.warning(
                "No compatible plugins found for platform={} architecture={}",
                platform, architecture,
            )
        return out

    def _check_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("PluginRegistry is not initialized")

    def get_plugin(self, plugin_id: str) -> Optional[PluginInfo]:
        self._check_initialized()
        for p in self._info.values():
            if p.id == plugin_id:
                return p
        return None
    
    
    async def get_plugin_at_version(
        self, plugin_name: str, version: str, platform: str, architecture: str
    ) -> Optional[PluginInfo]:
        if not self._initialized:
            await self.initialize()
        for versions in self._versions.values():
            e = versions.get(version)
            p = e.info if e else None
            if p and p.name == plugin_name and platform in p.platforms and architecture in p.architectures:
                return p
        return None

    async def get_latest_plugin(
        self, plugin_name: str, platform: str, architecture: str
    ) -> Optional[PluginInfo]:
        if not self._initialized:
            await self.initialize()
        candidates = [
            p for p in self._info.values() if p.name == plugin_name and platform in p.platforms and architecture in p.architectures
        ]
        if not candidates:
            return None
        latest = max(candidates, key=self._version_key)
        return latest
    
    @staticmethod
    def _version_key(p: PluginInfo) -> Version:
        try:
            return Version(p.version)
        except InvalidVersion:
            return Version("0.0.0")
    
    def _register(
        self,
        manifest: PluginManifest,
        plugin_dir: Path,
        artifact: PackageArtifact,
    ) -> None:
        versions = self._versions.setdefault(manifest.id, {})
        if manifest.version in versions:
            raise ValueError(f"Duplicate plugin {manifest.id}@{manifest.version}")

        package_url = self.artifact_store.url_for(artifact)
        info = manifest.to_info(
            packageUrl=package_url,
            checksum=artifact.checksum,
            size=artifact.size,
        )
        logger.debug("Registering plugin {} version {} with package URL {}", manifest.id, manifest.version, package_url)
        versions[manifest.version] = _Entry(info=info, manifest=manifest, path=plugin_dir)

        logger.info(
            "registered {id}@{ver} ({kind}, {n} caps, {size} bytes)",
            id=manifest.id, ver=manifest.version, kind=manifest.runtime.kind,
            n=len(manifest.capabilities), size=artifact.size,
        )

    # ── latest / compatibility ──────────────────────────────────

    @staticmethod
    def entry_compatibility(
        manifest: PluginManifest, app_version: Optional[str] = None,
    ) -> Compatibility:
        return check_compatibility(
            schema_version=manifest.schemaVersion,
            min_runtime=manifest.runtime.minPaiVersion,
            min_app=manifest.runtime.minAppVersion,
            runtime_version=RUNTIME_VERSION,
            app_version=app_version,
        )

    def _rebuild_latest(self) -> None:
        """Point the flat maps + capability registry at the newest runtime-compatible version."""
        self._info.clear()
        self._manifests.clear()
        self._paths.clear()
        self._capability_registry.clear()
        for plugin_id, versions in self._versions.items():
            ordered = sorted(versions.values(), key=lambda e: version_key(e.info.version), reverse=True)
            usable = [e for e in ordered if self.entry_compatibility(e.manifest).compatible]
            # None usable: still expose the newest so the catalog can say WHY it can't be installed,
            # but never advertise its capabilities to the planner.
            chosen = (usable or ordered)[0]
            self._info[plugin_id] = chosen.info
            self._manifests[plugin_id] = chosen.manifest
            self._paths[plugin_id] = chosen.path
            if not usable:
                logger.warning("{}: no version is compatible with runtime {}", plugin_id, RUNTIME_VERSION)
                continue
            for capability in chosen.manifest.capability_names:
                self._capability_registry.setdefault(capability, {})[plugin_id] = chosen.info

    # ── multi-version access ────────────────────────────────────

    def list_versions(self, plugin_id: str) -> List[PluginInfo]:
        """Every known version of a plugin, newest first."""
        entries = self._versions.get(plugin_id, {}).values()
        return [e.info for e in sorted(entries, key=lambda e: version_key(e.info.version), reverse=True)]

    def get_version_info(self, plugin_id: str, version: Optional[str]) -> Optional[PluginInfo]:
        if not version:
            return self._info.get(plugin_id)
        e = self._versions.get(plugin_id, {}).get(version)
        return e.info if e else None

    def get_version_manifest(self, plugin_id: str, version: Optional[str]) -> Optional[PluginManifest]:
        """Manifest of a specific version (``None`` version = newest usable)."""
        if not version:
            return self._manifests.get(plugin_id)
        e = self._versions.get(plugin_id, {}).get(version)
        return e.manifest if e else None

    def get_catalog(self) -> List[PluginInfo]:
        return list(self._info.values())

    def get_info(self, plugin_id: str, version: Optional[str] = None) -> Optional[PluginInfo]:
        return self.get_version_info(plugin_id, version)

    def require_info(self, plugin_id: str) -> PluginInfo:
        info = self._info.get(plugin_id)
        if info is None:
            raise ValueError(f"plugin {plugin_id!r} not found")
        return info
    
    def get_manifest(self, plugin_id: str, version: Optional[str] = None) -> Optional[PluginManifest]:
        return self.get_version_manifest(plugin_id, version)

    def get_plugins_for_capability(
        self,
        capability: str,
        platform: Optional[str] = None,
        architecture: Optional[str] = None,
    ) -> List[PluginInfo]:

        providers = self._capability_registry.get(capability, {})

        plugins = list(providers.values())

        if platform is not None:
            plugins = [
                p for p in plugins
                if platform in p.platforms
            ]

        if architecture is not None:
            plugins = [
                p for p in plugins
                if architecture in p.architectures
            ]

        return plugins

    def get_all_manifests(self, user_id: Optional[str] = None) -> List[PluginManifest]:
        return list(self._manifests.values())

    def get_all_capabilities(self) -> Dict[str, List[str]]:
        return {
            capability: list(providers.keys())
            for capability, providers in self._capability_registry.items()
        }

    def has_plugin(self, plugin_id: str, version: str) -> bool:
        if version is not None:
            return version in self._versions.get(plugin_id, {})
        return plugin_id in self._versions

    def has_capability(self, capability: str) -> bool:
        return capability in self._capability_registry

