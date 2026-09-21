import 'device_plugin.dart';
import 'models/plugin_capability.dart';

class PluginRegistry {
  final Map<String, DevicePlugin> _plugins = {};

  void add(DevicePlugin p) => _plugins[p.id] = p;
  void remove(String id) => _plugins.remove(id);
  DevicePlugin? byId(String id) => _plugins[id];
  List<DevicePlugin> all() => _plugins.values.toList();
  List<DevicePlugin> enabled() =>
      _plugins.values.where((p) => p.enabled).toList();

  ({DevicePlugin plugin, PluginCapability capability})? resolveCapability(
    String capId,
  ) {
    for (final p in enabled()) {
      for (final c in p.info.capabilityDetails) {
        if (c.id == capId) return (plugin: p, capability: c);
      }
    }
    return null;
  }
}