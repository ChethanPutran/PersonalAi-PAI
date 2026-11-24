import 'models/device_plugin.dart';
import 'models/plugin_capability.dart';

class PluginRegistry {
  final Map<String, DevicePlugin> _plugins = {};

  void add(DevicePlugin plugin) {                               
    _plugins[plugin.id] = plugin;
  }

  void remove(String id) {
    _plugins.remove(id);
  }

  DevicePlugin? byId(String id) => _plugins[id];

  List<DevicePlugin> all() => _plugins.values.toList();

  List<DevicePlugin> enabled() {
    return _plugins.values.where((p) => p.enabled).toList();
  }

  ({DevicePlugin plugin, PluginCapability capability})?
      resolveCapability(String capabilityId) {
    for (final plugin in enabled()) {
      for (final capability in plugin.capabilities) {
        if (capability.id == capabilityId) {
          return (
            plugin: plugin,
            capability: capability,
          );
        }
      }
    }

    return null;
  }
}