import 'package:flutter/foundation.dart';

import 'models/plugin_info_short.dart';
import 'plugin_service2.dart';

class PluginManager extends ChangeNotifier {
  final PluginService service;
  final String deviceId;

  final Map<String, PluginInfo> _plugins = {};

  bool _loading = false;

  PluginManager({
    required this.service,
    required this.deviceId,
  });

  List<PluginInfo> get plugins =>
      List.unmodifiable(_plugins.values);

  bool get isLoading => _loading;

  PluginInfo? get(String pluginId) {
    return _plugins[pluginId];
  }

  bool contains(String pluginId) {
    return _plugins.containsKey(pluginId);
  }

  Future<void> loadCatalog({
    required String platform,
    required String architecture,
  }) async {
    _loading = true;
    notifyListeners();

    try {
      final plugins = await service.getCatalog(
        platform: platform,
        architecture: architecture,
      );

      _plugins.clear();

      for (final plugin in plugins) {
        _plugins[plugin.id] = plugin;
      }
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  Future<void> install(
    String pluginId,
  ) async {
    final plugin = _require(pluginId);

    if (plugin.state != PluginState.available &&
        plugin.state != PluginState.error) {
      return;
    }

    plugin.clearError();
    plugin.setState(
      PluginState.installing,
    );

    try {
      /*
       * This only requests installation from backend.
       *
       * Backend will send:
       *
       * plugin.install
       *
       * through the device WebSocket.
       *
       * The state becomes INSTALLED only after
       * handleDeviceResult() receives successful
       * execution confirmation.
       */
      await service.installPlugin(
        deviceId: deviceId,
        pluginId: pluginId,
      );
    } catch (e) {
      plugin.setError(
        e.toString(),
      );

      rethrow;
    }

    notifyListeners();
  }

  Future<void> enable(
    String pluginId,
  ) async {
    final plugin = _require(pluginId);

    if (plugin.state != PluginState.installed &&
        plugin.state != PluginState.disabled) {
      throw StateError(
        'Plugin must be installed first',
      );
    }

    try {
      await service.enablePlugin(
        deviceId: deviceId,
        pluginId: pluginId,
      );

      /*
       * Backend has accepted the request.
       *
       * Final state will be confirmed by the
       * device result.
       */
    } catch (e) {
      plugin.setError(
        e.toString(),
      );

      rethrow;
    }
  }

  Future<void> disable(
    String pluginId,
  ) async {
    final plugin = _require(pluginId);

    if (plugin.state != PluginState.enabled) {
      return;
    }

    try {
      await service.disablePlugin(
        deviceId: deviceId,
        pluginId: pluginId,
      );
    } catch (e) {
      plugin.setError(
        e.toString(),
      );

      rethrow;
    }
  }

  /*
   * Called when a device command has completed successfully.
   */
  void handleCommandResult(
    Map<String, dynamic> message,
  ) {
    final success = message['success'] == true;

    final result = message['result'];

    if (result is! Map) {
      return;
    }

    final operation =
        result['operation']?.toString();

    final pluginId =
        result['plugin_id']?.toString();

    if (pluginId == null) {
      return;
    }

    final plugin = _plugins[pluginId];

    if (plugin == null) {
      return;
    }

    if (!success) {
      plugin.setError(
        message['error']?.toString() ??
            'Plugin operation failed',
      );

      notifyListeners();
      return;
    }

    switch (operation) {
      case 'plugin.install':
        plugin.clearError();
        plugin.setState(
          PluginState.installed,
        );
        break;

      case 'plugin.enable':
        plugin.clearError();
        plugin.setState(
          PluginState.enabled,
        );
        break;

      case 'plugin.disable':
        plugin.clearError();
        plugin.setState(
          PluginState.disabled,
        );
        break;
    }

    notifyListeners();
  }

  void handleCommandFailure(
    Map<String, dynamic> message,
  ) {
    final result = message['result'];

    if (result is! Map) {
      return;
    }

    final pluginId =
        result['plugin_id']?.toString();

    if (pluginId == null) {
      return;
    }

    final plugin = _plugins[pluginId];

    if (plugin == null) {
      return;
    }

    plugin.setError(
      message['error']?.toString() ??
          'Plugin operation failed',
    );

    notifyListeners();
  }

  Future<void> sync() async {
    await service.syncPlugins(
      deviceId: deviceId,
      plugins: plugins,
    );
  }

  PluginInfo _require(
    String pluginId,
  ) {
    final plugin = _plugins[pluginId];

    if (plugin == null) {
      throw StateError(
        'Plugin not found: $pluginId',
      );
    }

    return plugin;
  }
}