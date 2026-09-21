import 'device_plugin.dart';
import 'models/plugin_info_short.dart';

class PluginRegistry {
  final Map<String, DevicePlugin> _plugins = {};

  void register(
    String pluginId,
    DevicePlugin plugin,
  ) {
    _plugins[pluginId] = plugin;
  }

  DevicePlugin? get(String pluginId) {
    return _plugins[pluginId];
  }

  bool contains(String pluginId) {
    return _plugins.containsKey(pluginId);
  }

  Future<void> install(
    PluginInfo info,
  ) async {
    // Installation handled by PluginInstaller.
  }

  Future<void> enable(
    String pluginId,
  ) async {
    final plugin = _plugins[pluginId];

    if (plugin == null) {
      throw StateError(
        'Plugin not installed: $pluginId',
      );
    }

    await plugin.enable();
  }

  Future<void> disable(
    String pluginId,
  ) async {
    final plugin = _plugins[pluginId];

    if (plugin == null) {
      throw StateError(
        'Plugin not installed: $pluginId',
      );
    }

    await plugin.disable();
  }

  Future<dynamic> execute(
    String pluginId,
    String action,
    Map<String, dynamic> params,
  ) async {
    final plugin = _plugins[pluginId];

    if (plugin == null) {
      throw StateError(
        'Plugin not installed: $pluginId',
      );
    }

    return plugin.execute(action, params);
  }
}