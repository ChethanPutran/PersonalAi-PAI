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


MANIFEST_FILENAME = "manifest.json"

class PluginRegistry(Lifecycle):
    name = "plugin_registry"
    """
        Discovers plugin manifests and stores in the plugin store.
        Only latest version of each plugin is kept in the registry. The registry is responsible for:
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

                manifest = load_manifest_file(manifest_path)

                platform = DevicePlatform(platform_dir.name)

                artifact = self.packager.package(
                    manifest=manifest,
                    version_dir=version_dir,
                    platform=platform,
                )

                self._register(manifest, version_dir, artifact)


    async def get_compatible_plugins(
        self, platform: str, architecture: str) -> List[PluginInfo]:
        if not self._initialized:
            await self.initialize()
        plugins = [
            p for p in self._info.values()
            if platform in p.platforms and architecture in p.architectures
        ]
        if not plugins:
            logger.warning(
                "No compatible plugins found for platform={} architecture={}",
                platform, architecture,
            )
            return []

        latest_plugins = {}
        for p in plugins:
            if p.id not in latest_plugins or self._version_key(p) > self._version_key(latest_plugins[p.id]):
                latest_plugins[p.id] = p

        plugins = list(latest_plugins.values())

        return plugins

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
        for p in self._info.values():
            if p.name == plugin_name and p.version == version and platform in p.platforms and architecture in p.architectures:
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
        if manifest.id in self._manifests:
            raise ValueError(f"Duplicate plugin ID: {manifest.id}")

        # build info — packageUrl and checksum 
        package_url = self.artifact_store.url_for(artifact)
        info = manifest.to_info(
            packageUrl=package_url,
            checksum=artifact.checksum,
            size=artifact.size,
        )
        print(f"Registering plugin {manifest.id} version {manifest.version} with package URL {package_url}")
        print(info)

        self._info[manifest.id] = info
        self._manifests[manifest.id] = manifest
        self._paths[manifest.id] = plugin_dir

        for capability in manifest.capability_names:
            providers = self._capability_registry.setdefault(capability, {})
            existing = providers.get(capability)
            if existing is not None:
                raise ValueError(
                    f"Capability '{capability}' is already provided by "
                    f"plugin '{existing}'"
                )
            providers[manifest.id] = info

        logger.info(
                    "registered {id}@{ver} ({kind}, {n} caps, {size} bytes)",
                    id=manifest.id,
                    ver=manifest.version,
                    kind=manifest.runtime.kind,
                    n=len(manifest.capabilities),
                    size=artifact.size,
                )
        
    def get_catalog(self) -> List[PluginInfo]:
        return list(self._info.values())

    def get_info(self, plugin_id: str) -> Optional[PluginInfo]:
        return self._info.get(plugin_id)

    def require_info(self, plugin_id: str) -> PluginInfo:
        info = self._info.get(plugin_id)
        if info is None:
            raise ValueError(f"plugin {plugin_id!r} not found")
        return info
    
    def get_manifest(self, plugin_id: str) -> Optional[PluginManifest]:
        return self._manifests.get(plugin_id)

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
            manifest = self._manifests.get(plugin_id)
            return manifest is not None and manifest.version == version
        return plugin_id in self._manifests

    def has_capability(self, capability: str) -> bool:
        return capability in self._capability_registry

