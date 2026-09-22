from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Type

from loguru import logger

from pai.plugins.base import BasePlugin
from pai.plugins.models import PluginManifest


class PluginRegistry:
    """
    Discovers plugin manifests and implementation classes.

    Responsibilities:
        - Discover plugin directories
        - Load manifest.json
        - Load plugin implementation class
        - Register plugin metadata
        - Resolve capabilities to plugins

    Does NOT:
        - Start plugins
        - Stop plugins
        - Execute plugins
        - Manage users
        - Manage devices
        - Perform authorization
    """

    def __init__(self, plugins_dir: Optional[Path] = None) -> None:
        self.plugins_dir = (
            plugins_dir
            if plugins_dir is not None
            else Path(__file__).parent
        )

        self._manifests: Dict[str, PluginManifest] = {}
        self._plugin_classes: Dict[str, Type[BasePlugin]] = {}
        self._capability_registry: Dict[str, str] = {}

    async def discover(self) -> None:
        """Discover all plugins under the plugins directory."""

        self._manifests.clear()
        self._plugin_classes.clear()
        self._capability_registry.clear()

        if not self.plugins_dir.exists():
            raise FileNotFoundError(
                f"Plugin directory does not exist: {self.plugins_dir}"
            )

        for plugin_dir in sorted(self.plugins_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue

            if plugin_dir.name.startswith("_"):
                continue

            manifest_path = plugin_dir / "manifest.json"


            if not manifest_path.exists():
                logger.warning(
                    f"Skipping '{plugin_dir.name}': "
                    "manifest.json not found"
                )
                continue

            try:
                manifest = self._load_manifest(manifest_path)

                if manifest.runtime.kind != "server":
                    logger.debug(
                        "Skipping '%s' in server plugin dir: kind=%s",
                        manifest.id, manifest.runtime.kind,
                    )
                    continue

                plugin_class = self._load_plugin_class(
                    plugin_dir,
                    manifest,
                )

                self._register(
                    manifest,
                    plugin_class,
                )

                logger.info(
                    f"Registered plugin '{manifest.id}' "
                    f"with {len(manifest.capabilities)} capabilities"
                )

            except Exception as exc:
                logger.error(
                    f"Failed to register plugin "
                    f"'{plugin_dir.name}': {exc}"
                )

    def _load_manifest(
        self,
        manifest_path: Path,
    ) -> PluginManifest:
        with manifest_path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return PluginManifest.from_dict(data)

    def _load_plugin_class(
        self,
        plugin_dir: Path,
        manifest: PluginManifest,
    ) -> Type[BasePlugin]:
        module_path = plugin_dir / manifest.runtime.entry_point

        if not module_path.exists():
            raise FileNotFoundError(
                f"Plugin entry point not found: {module_path}"
            )

        module_name = (
            f"pai.plugins.dynamic."
            f"{manifest.id.replace('-', '_')}"
        )

        spec = importlib.util.spec_from_file_location(
            module_name,
            module_path,
        )

        if spec is None or spec.loader is None:
            raise ImportError(
                f"Unable to load module: {module_path}"
            )

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        plugin_class = getattr(
            module,
            manifest.runtime.class_name,
            None,
        )

        if plugin_class is None:
            raise ImportError(
                f"Class '{manifest.runtime.class_name}' "
                f"not found in {module_path}"
            )

        if not inspect.isclass(plugin_class):
            raise TypeError(
                f"{manifest.runtime.class_name} is not a class"
            )

        if not issubclass(plugin_class, BasePlugin):
            raise TypeError(
                f"{manifest.runtime.class_name} must inherit "
                "from BasePlugin"
            )

        return plugin_class

    def _register(
        self,
        manifest: PluginManifest,
        plugin_class: Type[BasePlugin],
    ) -> None:
        if manifest.id in self._manifests:
            raise ValueError(
                f"Duplicate plugin ID: {manifest.id}"
            )

        self._manifests[manifest.id] = manifest
        self._plugin_classes[manifest.id] = plugin_class

        for capability in manifest.capability_names:
            existing = self._capability_registry.get(capability)

            if existing is not None:
                raise ValueError(
                    f"Capability '{capability}' is already "
                    f"provided by plugin '{existing}'"
                )

            self._capability_registry[capability] = manifest.id

    def get_manifest(
        self,
        plugin_id: str,
    ) -> Optional[PluginManifest]:
        return self._manifests.get(plugin_id)

    def get_plugin_class(
        self,
        plugin_id: str,
    ) -> Optional[Type[BasePlugin]]:
        return self._plugin_classes.get(plugin_id)

    def get_plugin_for_capability(
        self,
        capability: str,
    ) -> Optional[str]:
        return self._capability_registry.get(capability)

    def get_all_manifests(self, user_id: Optional[str] = None) -> List[PluginManifest]:
        return list(self._manifests.values())

    def get_all_capabilities(self) -> Dict[str, str]:
        return self._capability_registry.copy()

    def has_plugin(self, plugin_id: str) -> bool:
        return plugin_id in self._manifests

    def has_capability(self, capability: str) -> bool:
        return capability in self._capability_registry