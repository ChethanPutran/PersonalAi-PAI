"""Plugin Manager for dynamic capability loading."""

import importlib
import inspect
from typing import Dict, Any, List, Optional, Type
from pathlib import Path
from loguru import logger

from pai.plugins.base_plugin import BasePlugin


class PluginManager:
    """
    Manages plugin lifecycle, discovery, and execution.
    
    Responsibilities:
    - Plugin installation/uninstallation
    - Dynamic loading
    - Permission management
    - Capability registry
    """
    
    def __init__(self, kernel=None):
        self.kernel = kernel
        self._plugins: Dict[str, BasePlugin] = {}
        self._capability_registry: Dict[str, str] = {}  # capability -> plugin
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize the plugin manager."""
        # Discover and load plugins
        await self.discover_plugins()
        self._initialized = True
        logger.info(f"Plugin manager initialized with {len(self._plugins)} plugins")
    
    async def discover_plugins(self) -> None:
        """Discover and load available plugins."""
        plugins_dir = Path(__file__).parent
        
        for file_path in plugins_dir.glob("*_plugin.py"):
            if file_path.stem == "base_plugin":
                continue
            
            module_name = f"pai.plugins.{file_path.stem}"
            try:
                module = importlib.import_module(module_name)
                
                # Find plugin classes in module
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BasePlugin) and obj != BasePlugin:
                        plugin_instance = obj()
                        plugin_instance.kernel = self.kernel
                        await plugin_instance.initialize()
                        
                        self._plugins[plugin_instance.name] = plugin_instance
                        
                        # Register capabilities
                        for capability in plugin_instance.get_capabilities():
                            self._capability_registry[capability] = plugin_instance.name
                        
                        logger.info(f"Loaded plugin: {plugin_instance.name}")
                        
            except Exception as e:
                logger.error(f"Failed to load plugin {module_name}: {e}")
    
    async def install_plugin(self, plugin_path: str) -> bool:
        """
        Install a new plugin dynamically.
        
        Args:
            plugin_path: Path to plugin module or package
            
        Returns:
            True if installation successful
        """
        # TODO: Implement plugin installation from package
        logger.info(f"Installing plugin from {plugin_path}")
        return False
    
    async def uninstall_plugin(self, plugin_name: str) -> bool:
        """
        Uninstall a plugin.
        
        Args:
            plugin_name: Name of plugin to uninstall
            
        Returns:
            True if uninstallation successful
        """
        if plugin_name not in self._plugins:
            logger.warning(f"Plugin {plugin_name} not found")
            return False
        
        plugin = self._plugins[plugin_name]
        await plugin.shutdown()
        
        # Remove capabilities
        for capability, p_name in list(self._capability_registry.items()):
            if p_name == plugin_name:
                del self._capability_registry[capability]
        
        del self._plugins[plugin_name]
        logger.info(f"Uninstalled plugin: {plugin_name}")
        return True
    
    async def execute_plugin(self, plugin_name: str, action: str, params: Dict[str, Any]) -> Any:
        """
        Execute a plugin action.
        
        Args:
            plugin_name: Name of the plugin
            action: Action to execute
            params: Action parameters
            
        Returns:
            Action result
        """
        if plugin_name not in self._plugins:
            raise ValueError(f"Plugin {plugin_name} not found")
        
        plugin = self._plugins[plugin_name]
        
        # Check permissions
        if self.kernel:
            has_permission = await self.kernel.security_manager.check_permission(
                plugin_name, action
            )
            if not has_permission:
                raise PermissionError(f"No permission for {plugin_name}.{action}")
        
        return await plugin.execute(action, params)
    
    async def get_capability(self, capability: str) -> Optional[str]:
        """
        Get plugin name for a capability.
        
        Args:
            capability: Requested capability
            
        Returns:
            Plugin name or None
        """
        return self._capability_registry.get(capability)
    
    async def list_plugins(self) -> List[Dict[str, Any]]:
        """List all loaded plugins."""
        return [
            {
                "name": plugin.name,
                "capabilities": plugin.get_capabilities(),
                "running": plugin._running
            }
            for plugin in self._plugins.values()
        ]
    
    async def handle_plugin_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Route events to appropriate plugins."""
        for plugin in self._plugins.values():
            try:
                await plugin.handle_event(event_type, data)
            except Exception as e:
                logger.error(f"Error in plugin {plugin.name} handling event: {e}")
    
    async def shutdown(self) -> None:
        """Shutdown all plugins."""
        for plugin in self._plugins.values():
            try:
                await plugin.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down plugin {plugin.name}: {e}")
        
        self._plugins.clear()
        self._capability_registry.clear()
        logger.info("Plugin manager shutdown")