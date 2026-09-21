import 'device_plugin.dart';
import 'models/plugin_info.dart';
import 'plugin_factory.dart';

class PluginRegistry {
  final Map<String, DevicePlugin> _plugins = {};

  final Map<String, PluginFactory> _factories = {};

  void registerFactory(
    String pluginId,
    PluginFactory factory,
  ) {
    _factories[pluginId] =
        factory;
  }

  DevicePlugin? get(
    String pluginId,
  ) {
    return _plugins[pluginId];
  }

  bool contains(
    String pluginId,
  ) {
    return _plugins.containsKey(pluginId);
  }

  Future<void> install(
    PluginInfo info,
  ) async {
    var plugin =
        _plugins[info.id];

    /*
     * Create plugin implementation
     * from registered factory.
     */
    if (plugin == null) {
      final factory =
          _factories[info.id];

      if (factory == null) {
        throw UnsupportedError(
          'No plugin factory registered '
          'for ${info.id}',
        );
      }

      plugin = factory(info);

      _plugins[info.id] =
          plugin;
    }

    await plugin.install();
  }

  Future<void> enable(
    String pluginId,
  ) async {
    final plugin =
        _require(pluginId);

    await plugin.enable();
  }

  Future<void> disable(
    String pluginId,
  ) async {
    final plugin =
        _require(pluginId);

    await plugin.disable();
  }

  Future<dynamic> execute(
    String pluginId,
    String action,
    Map<String, dynamic> params,
  ) async {
    final plugin =
        _require(pluginId);

    return plugin.execute(
      action,
      params,
    );
  }

  DevicePlugin _require(
    String pluginId,
  ) {
    final plugin =
        _plugins[pluginId];

    if (plugin == null) {
      throw StateError(
        'Plugin not installed: $pluginId',
      );
    }

    return plugin;
  }
}