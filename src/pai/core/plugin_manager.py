"""
Plugin Manager - Modular plugin system for PAI
"""
import logging
import importlib.util
import sys
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


@dataclass
class PluginManifest:
    """Plugin manifest"""
    id: str
    name: str
    version: str
    plugin_type: str  # sensor, intelligence, action, integration
    entry_point: str
    description: str
    permissions: List[str]
    dependencies: List[str]
    config_schema: Dict[str, Any]


@dataclass
class PluginInstance:
    """Plugin instance"""
    id: str
    manifest: PluginManifest
    module: Any
    is_loaded: bool
    is_enabled: bool
    config: Dict[str, Any]
    state: Dict[str, Any]
    created_at: datetime
    last_used: Optional[datetime] = None


class PluginManager:
    """
    Manages modular plugins for the Personal AI system.
    
    Responsibilities:
    - Plugin discovery and loading
    - Capability registry
    - Plugin isolation and permissions
    - Plugin versioning
    - Dynamic plugin installation/uninstallation
    """
    
    def __init__(self, plugins_dir: str = "./plugins", config: Dict[str, Any] = None):
        """Initialize plugin manager"""
        self.plugins_dir = Path(plugins_dir)
        self.config = config or {}
        self.plugins: Dict[str, PluginInstance] = {}
        self.plugin_registry: Dict[str, List[str]] = {}  # Capability -> Plugins
        self.event_bus = None
        logger.info(f"Plugin Manager initialized with plugins dir: {self.plugins_dir}")
    
    async def initialize(self, event_bus=None):
        """Initialize plugin manager"""
        self.event_bus = event_bus
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        await self.discover_plugins()
        logger.info("Plugin Manager initialized")
    
    async def discover_plugins(self) -> List[PluginManifest]:
        """Discover available plugins"""
        manifests = []
        
        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory does not exist: {self.plugins_dir}")
            return manifests
        
        for plugin_dir in self.plugins_dir.iterdir():
            if not plugin_dir.is_dir():
                continue
            
            manifest_file = plugin_dir / "manifest.json"
            if not manifest_file.exists():
                logger.debug(f"No manifest found in {plugin_dir}")
                continue
            
            try:
                with open(manifest_file, 'r') as f:
                    manifest_data = json.load(f)
                
                manifest = PluginManifest(**manifest_data)
                manifests.append(manifest)
                logger.info(f"Discovered plugin: {manifest.name} v{manifest.version}")
            except Exception as e:
                logger.error(f"Failed to load manifest from {plugin_dir}: {e}")
        
        if self.event_bus:
            await self.event_bus.publish("plugin/discovery_completed", {
                "count": len(manifests)
            })
        
        return manifests
    
    async def load_plugin(self, manifest: PluginManifest) -> Optional[PluginInstance]:
        """Load a plugin"""
        try:
            # Check if already loaded
            if manifest.id in self.plugins:
                logger.info(f"Plugin already loaded: {manifest.name}")
                return self.plugins[manifest.id]
            
            # Load plugin module
            plugin_dir = self.plugins_dir / manifest.name
            plugin_file = plugin_dir / manifest.entry_point
            
            if not plugin_file.exists():
                logger.error(f"Plugin entry point not found: {plugin_file}")
                return None
            
            # Import module
            spec = importlib.util.spec_from_file_location(manifest.name, plugin_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[manifest.name] = module
            spec.loader.exec_module(module)
            
            # Create plugin instance
            instance = PluginInstance(
                id=manifest.id,
                manifest=manifest,
                module=module,
                is_loaded=True,
                is_enabled=True,
                config={},
                state={},
                created_at=datetime.utcnow()
            )
            
            self.plugins[manifest.id] = instance
            
            # Register capabilities
            if hasattr(module, 'get_capabilities'):
                capabilities = module.get_capabilities()
                for capability in capabilities:
                    if capability not in self.plugin_registry:
                        self.plugin_registry[capability] = []
                    self.plugin_registry[capability].append(manifest.id)
            
            logger.info(f"Plugin loaded: {manifest.name}")
            
            # Emit event
            if self.event_bus:
                await self.event_bus.publish("plugin/loaded", {
                    "plugin_id": manifest.id,
                    "name": manifest.name,
                    "version": manifest.version
                })
            
            return instance
        except Exception as e:
            logger.error(f"Failed to load plugin {manifest.name}: {e}", exc_info=True)
            return None
    
    async def unload_plugin(self, plugin_id: str) -> bool:
        """Unload a plugin"""
        if plugin_id not in self.plugins:
            logger.warning(f"Plugin not found: {plugin_id}")
            return False
        
        instance = self.plugins[plugin_id]
        
        # Remove from registry
        for capability in list(self.plugin_registry.keys()):
            if plugin_id in self.plugin_registry[capability]:
                self.plugin_registry[capability].remove(plugin_id)
        
        del self.plugins[plugin_id]
        logger.info(f"Plugin unloaded: {instance.manifest.name}")
        
        # Emit event
        if self.event_bus:
            await self.event_bus.publish("plugin/unloaded", {
                "plugin_id": plugin_id,
                "name": instance.manifest.name
            })
        
        return True
    
    async def enable_plugin(self, plugin_id: str) -> bool:
        """Enable a plugin"""
        if plugin_id not in self.plugins:
            logger.warning(f"Plugin not found: {plugin_id}")
            return False
        
        instance = self.plugins[plugin_id]
        instance.is_enabled = True
        logger.info(f"Plugin enabled: {instance.manifest.name}")
        
        if self.event_bus:
            await self.event_bus.publish("plugin/enabled", {"plugin_id": plugin_id})
        
        return True
    
    async def disable_plugin(self, plugin_id: str) -> bool:
        """Disable a plugin"""
        if plugin_id not in self.plugins:
            logger.warning(f"Plugin not found: {plugin_id}")
            return False
        
        instance = self.plugins[plugin_id]
        instance.is_enabled = False
        logger.info(f"Plugin disabled: {instance.manifest.name}")
        
        if self.event_bus:
            await self.event_bus.publish("plugin/disabled", {"plugin_id": plugin_id})
        
        return True
    
    async def get_plugin(self, plugin_id: str) -> Optional[PluginInstance]:
        """Get plugin instance"""
        return self.plugins.get(plugin_id)
    
    async def get_plugins_by_capability(self, capability: str) -> List[PluginInstance]:
        """Get plugins that provide a capability"""
        plugin_ids = self.plugin_registry.get(capability, [])
        plugins = []
        for plugin_id in plugin_ids:
            instance = self.plugins.get(plugin_id)
            if instance and instance.is_enabled:
                plugins.append(instance)
        return plugins
    
    async def list_plugins(self) -> List[Dict[str, Any]]:
        """List all plugins"""
        plugins_list = []
        for instance in self.plugins.values():
            plugins_list.append({
                "id": instance.id,
                "name": instance.manifest.name,
                "version": instance.manifest.version,
                "type": instance.manifest.plugin_type,
                "is_enabled": instance.is_enabled,
                "is_loaded": instance.is_loaded
            })
        return plugins_list
    
    async def execute_plugin_method(self,
                                   plugin_id: str,
                                   method_name: str,
                                   *args,
                                   **kwargs) -> Any:
        """Execute a plugin method"""
        instance = self.plugins.get(plugin_id)
        if not instance or not instance.is_enabled:
            logger.warning(f"Plugin not available: {plugin_id}")
            return None
        
        try:
            if hasattr(instance.module, method_name):
                method = getattr(instance.module, method_name)
                instance.last_used = datetime.utcnow()
                
                if callable(method):
                    result = method(*args, **kwargs)
                    # Handle async methods
                    import asyncio
                    if asyncio.iscoroutine(result):
                        result = await result
                    return result
            else:
                logger.warning(f"Method not found in plugin: {method_name}")
                return None
        except Exception as e:
            logger.error(f"Plugin method execution failed: {e}", exc_info=True)
            return None
    
    async def get_status(self) -> Dict[str, Any]:
        """Get plugin manager status"""
        return {
            "total_plugins": len(self.plugins),
            "loaded_plugins": len([p for p in self.plugins.values() if p.is_loaded]),
            "enabled_plugins": len([p for p in self.plugins.values() if p.is_enabled]),
            "capabilities": len(self.plugin_registry),
            "plugins": await self.list_plugins()
        }
