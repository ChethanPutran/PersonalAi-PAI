import 'package:flutter/foundation.dart';

import '../core/api/api_client.dart';
import 'models/plugin_info.dart';
import 'plugin_service.dart';


/// UI-facing ChangeNotifier. Holds the catalog and drives the service.
class PluginManager extends ChangeNotifier {
  final PluginService service;
  final ApiClient api;
  final String deviceId;

  final Map<String, PluginInfo> _plugins = {};
  bool _loading = false;

  PluginManager({
    required this.service,
    required this.api,
    required this.deviceId,
  });

  List<PluginInfo> get plugins => List.unmodifiable(_plugins.values);
  bool get isLoading => _loading;
  PluginInfo? get(String id) => _plugins[id];
  bool contains(String id) => _plugins.containsKey(id);

  // ---------------------------------------------------------------
  // Catalog
  // ---------------------------------------------------------------

  Future<void> loadCatalog({
    required String platform,
    required String architecture,
  }) async {
    _loading = true;
    notifyListeners();

    try {
      final res = await api.get(
        '/plugins/catalog',
        query: {'platform': platform, 'architecture': architecture},
      );

      final raw = res['plugins'];
      if (raw is! List) throw Exception('Invalid catalog response');

      final installedMap = await service.readInstalled();
      final localManifests = await service.discoverLocal();
      final localIds = localManifests.map((m) => m.id).toSet();

      debugPrint(
        '[PluginManager] loadCatalog: '
        '${localIds.length} local, ${raw.length} catalog',
      );

      _plugins.clear();
      for (final item in raw) {
        final info = PluginInfo.fromCatalogJson(
          Map<String, dynamic>.from(item as Map),
        );

        if (localIds.contains(info.id)) {
          final entry = installedMap[info.id];
          final enabled = entry is Map && entry['enabled'] == true;
          info.state = enabled ? PluginState.enabled : PluginState.disabled;
          debugPrint(
            '[PluginManager]   ${info.id}: installed, enabled=$enabled',
          );
        } else {
          info.state = PluginState.available;
        }

        _plugins[info.id] = info;
      }
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  // ---------------------------------------------------------------
  // Install / uninstall
  // ---------------------------------------------------------------

  Future<void> install(String pluginId, String version) async {
    final plugin = _require(pluginId);
    plugin.state = PluginState.installing;
    plugin.error = null;
    notifyListeners();

    try {
      // 1. Local: download + verify + persist.
      await service.install(pluginId, version);

      // 2. Remote: report success to the backend.
      try {
        await api.post(
          '/plugins/$pluginId/report-install',
          {
            'device_id': deviceId,
            'version': version,
            'success': true,
          },
        );
      } catch (e) {
        debugPrint('[PluginManager] report-install failed: $e');
      }

      plugin.state = PluginState.installed;
    } catch (e) {
      try {
        await api.post(
          '/plugins/$pluginId/report-install',
          {
            'device_id': deviceId,
            'version': version,
            'success': false,
            'error': e.toString(),
          },
        );
      } catch (_) {}

      plugin.state = PluginState.error;
      plugin.error = e.toString();
      rethrow;
    } finally {
      notifyListeners();
    }
  }

  Future<void> uninstall(String pluginId) async {
    try {
      await service.uninstall(pluginId);

      try {
        await api.post(
          '/plugins/$pluginId/report-uninstall',
          {'device_id': deviceId, 'success': true},
        );
      } catch (e) {
        debugPrint('[PluginManager] report-uninstall failed: $e');
      }
    } finally {
      _plugins.remove(pluginId);
      notifyListeners();
    }
  }

  // ---------------------------------------------------------------
  // Enable / disable
  // ---------------------------------------------------------------

  Future<void> enable(String pluginId) async {
    final plugin = _require(pluginId);

    try {
      // 1. Local: load the native module.
      final ok = await service.enable(pluginId);
      if (!ok) {
        throw StateError('Native load failed for $pluginId');
      }

      // 2. Remote: record authorization on the backend.
      try {
        await api.post(
          '/plugins/$pluginId/enable',
          {'device_id': deviceId},
        );
      } catch (e) {
        debugPrint('[PluginManager] backend enable failed: $e');
      }

      plugin.state = PluginState.enabled;
      plugin.error = null;
    } catch (e) {
      plugin.state = PluginState.error;
      plugin.error = e.toString();
      rethrow;
    } finally {
      notifyListeners();
    }
  }

  Future<void> disable(String pluginId) async {
    final plugin = _require(pluginId);

    try {
      await service.disable(pluginId);

      try {
        await api.post(
          '/plugins/$pluginId/disable',
          {'device_id': deviceId},
        );
      } catch (e) {
        debugPrint('[PluginManager] backend disable failed: $e');
      }

      plugin.state = PluginState.disabled;
    } catch (e) {
      plugin.state = PluginState.error;
      plugin.error = e.toString();
      rethrow;
    } finally {
      notifyListeners();
    }
  }

  // ---------------------------------------------------------------
  // Reconcile
  // ---------------------------------------------------------------

  Future<void> reconcileWithBackend() async {
    debugPrint('[PluginManager] Reconciling with backend...');

    try {
      final snapshot = await service.localSnapshot();

      debugPrint(
        '[PluginManager]   local snapshot: '
        '${snapshot.map((p) => "${p['id']}@${p['version']} enabled=${p['enabled']}").join(", ")}',
      );

      final res = await api.post(
        '/plugins/device/$deviceId/reconcile',
        {'plugins': snapshot},
      );

      debugPrint(
        '[PluginManager]   backend response: '
        'added=${res['added']} updated=${res['updated']} removed=${res['removed']}',
      );
    } catch (e, st) {
      debugPrint('[PluginManager] reconcile failed: $e');
      debugPrintStack(stackTrace: st);
    }
  }

  // ---------------------------------------------------------------
  // Command-result hooks (called by PaiApp)
  // ---------------------------------------------------------------

  void handleCommandResult(Map<String, dynamic> message) {
    final success = message['success'] == true;
    final result = message['result'];
    if (result is! Map) return;

    final operation = result['operation']?.toString();
    final pluginId = result['plugin_id']?.toString();
    if (pluginId == null) return;

    final plugin = _plugins[pluginId];
    if (plugin == null) return;

    if (!success) {
      plugin.state = PluginState.error;
      plugin.error = message['error']?.toString() ?? 'Plugin operation failed';
      notifyListeners();
      return;
    }

    switch (operation) {
      case 'plugin.install':
        plugin.state = PluginState.installed;
        plugin.error = null;
        break;
      case 'plugin.enable':
        plugin.state = PluginState.enabled;
        plugin.error = null;
        break;
      case 'plugin.disable':
        plugin.state = PluginState.disabled;
        plugin.error = null;
        break;
      case 'plugin.uninstall':
        _plugins.remove(pluginId);
        break;
    }

    notifyListeners();
  }

  void handleCommandFailure(Map<String, dynamic> message) {
    final result = message['result'];
    if (result is! Map) return;
    final pluginId = result['plugin_id']?.toString();
    if (pluginId == null) return;

    final plugin = _plugins[pluginId];
    if (plugin == null) return;

    plugin.state = PluginState.error;
    plugin.error = message['error']?.toString() ?? 'Plugin operation failed';
    notifyListeners();
  }

  PluginInfo _require(String pluginId) {
    final p = _plugins[pluginId];
    if (p == null) throw StateError('Plugin not found: $pluginId');
    return p;
  }
}