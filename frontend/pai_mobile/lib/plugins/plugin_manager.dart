import 'package:flutter/foundation.dart';

import '../core/api/api_client.dart';
import 'models/catalog_response.dart';
import 'models/plugin_info.dart';
import 'plugin_service.dart';


// ---------------------------------------------------------------------------
// Remote operation tracking
// ---------------------------------------------------------------------------

enum RemotePhase { idle, installing, failed, succeeded }

class RemoteOp {
  RemotePhase phase;
  String? error;
  RemoteOp({this.phase = RemotePhase.idle, this.error});
}

/// UI-facing ChangeNotifier. Holds the catalog, grouped by device
/// compatibility, and drives the service.
///
/// Two views over the same data:
///
///   • `thisDevicePlugins` — plugins compatible with the device running
///     this app. UI can install / enable / disable / configure these.
///
///   • `otherDeviceGroups` — plugins compatible with *other* devices the
///     user owns but not with this one. UI can request remote installs
///     for these, but not local install / enable.
///
///   • `plugins` — flat union of both, used only by `get(id)` and the
///     command-result handlers.
class PluginManager extends ChangeNotifier {
  final PluginService service;
  final ApiClient api;
  final String deviceId;

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------

  final Map<String, PluginInfo> _plugins = {};
  final Set<String> _thisDeviceIds = {};

  DeviceDescriptor? _thisDevice;
  List<PluginInfo> _thisDevicePlugins = [];
  List<OtherDeviceGroup> _otherDeviceGroups = [];

  bool _loading = false;

  /// Per-plugin capability config cache.
  /// Shape: { pluginId: { capabilityId: { 'enabled': bool, 'params': {...} } } }
  final Map<String, Map<String, Map<String, dynamic>>> _capConfigCache = {};

  PluginManager({
    required this.service,
    required this.api,
    required this.deviceId,
  });

  // ------------------------------------------------------------------
  // Getters
  // ------------------------------------------------------------------

  /// Flat union of this-device + other-device plugins. Used by
  /// `get(id)` and the WS command-result handlers; UI screens should
  /// prefer the grouped lists.
  List<PluginInfo> get plugins => List.unmodifiable(_plugins.values);

  /// Plugins compatible with the device running this app.
  List<PluginInfo> get thisDevicePlugins =>
      List.unmodifiable(_thisDevicePlugins);

  /// Plugins compatible with other devices the user owns, grouped.
  List<OtherDeviceGroup> get otherDeviceGroups =>
      List.unmodifiable(_otherDeviceGroups);

  DeviceDescriptor? get thisDevice => _thisDevice;
  bool get isLoading => _loading;

  PluginInfo? get(String id) => _plugins[id];
  bool contains(String id) => _plugins.containsKey(id);


  /// Per-(device,plugin) state for remote operations.
  /// Key format: "<deviceId>::<pluginId>"
  final Map<String, RemoteOp> _remoteOps = {};
  String _rk(String deviceId, String pluginId) => '$deviceId::$pluginId';

  RemoteOp remoteStateFor(String deviceId, String pluginId) {
    return _remoteOps[_rk(deviceId, pluginId)] ?? RemoteOp();
  }


  // -------------------------------------------------------------------
  // Remote install with polling
  // -------------------------------------------------------------------

  /// Request install on a remote device and poll until resolved.
  ///
  /// The POST returns immediately with `install_requested`; the actual
  /// completion happens when the agent reports back. We poll
  /// `/plugins/device/{id}/installed` until the plugin's row reports
  /// `is_installed=true` with the expected version, or `last_error`
  /// becomes something other than the `install_requested` marker.
  Future<void> installOnDevice(
    String pluginId,
    String version,
    String targetDeviceId,
  ) async {
    final key = _rk(targetDeviceId, pluginId);
    _remoteOps[key] = RemoteOp(phase: RemotePhase.installing);
    notifyListeners();

    try {
      await api.post('/plugins/$pluginId/install', {
        'device_id': targetDeviceId,
        'version': version,
      });
    } catch (e) {
      _remoteOps[key] = RemoteOp(
        phase: RemotePhase.failed,
        error: e.toString(),
      );
      notifyListeners();
      rethrow;
    }

    // Kick off the poll in the background — do not await so the UI
    // stays responsive while the agent downloads / installs.
    _pollRemoteInstall(targetDeviceId, pluginId, version);
  }

  Future<void> _pollRemoteInstall(
    String deviceId,
    String pluginId,
    String expectedVersion, {
    Duration interval = const Duration(seconds: 2),
    Duration timeout = const Duration(minutes: 5),
  }) async {
    final key = _rk(deviceId, pluginId);
    final deadline = DateTime.now().add(timeout);

    while (DateTime.now().isBefore(deadline)) {
      await Future.delayed(interval);

      try {
        final res = await api.get('/plugins/device/$deviceId/installed');
        final plugins = (res['plugins'] as List? ?? []);
        final entry = plugins.firstWhere(
          (p) => p is Map && p['plugin_id'] == pluginId,
          orElse: () => null,
        );
        if (entry == null) continue;

        final err = entry['last_error']?.toString();
        final isInstalled = entry['is_installed'] == true;
        final installedVersion = entry['version']?.toString();

        // Real error — anything other than the "install_requested"
        // marker the route sets before pushing to the agent.
        if (err != null && err.isNotEmpty && err != 'install_requested') {
          _remoteOps[key] = RemoteOp(
            phase: RemotePhase.failed,
            error: err,
          );
          notifyListeners();
          return;
        }

        if (isInstalled && installedVersion == expectedVersion) {
          _remoteOps[key] = RemoteOp(phase: RemotePhase.succeeded);
          notifyListeners();
          // Refresh so the tile updates to the installed view.
          await loadCatalog();
          return;
        }
      } catch (e) {
        // Transient — backend hiccup, network blip. Keep polling.
        debugPrint('[PluginManager] remote poll error: $e');
      }
    }

    _remoteOps[key] = RemoteOp(
      phase: RemotePhase.failed,
      error: 'Timed out after ${timeout.inMinutes} minutes. '
             'Check the device\'s agent log.',
    );
    notifyListeners();
  }

  /// Retry — resets state and re-issues the request.
  Future<void> retryInstallOnDevice(
    String pluginId,
    String version,
    String targetDeviceId,
  ) async {
    _remoteOps[_rk(targetDeviceId, pluginId)] = RemoteOp();
    notifyListeners();
    return installOnDevice(pluginId, version, targetDeviceId);
  }

  // -------------------------------------------------------------------
  // Remote enable / disable
  // -------------------------------------------------------------------

  Future<void> enableOnDevice(String pluginId, String targetDeviceId) async {
    await api.post('/plugins/$pluginId/enable', {'device_id': targetDeviceId});
    await loadCatalog();
  }

  Future<void> disableOnDevice(String pluginId, String targetDeviceId) async {
    await api.post('/plugins/$pluginId/disable', {'device_id': targetDeviceId});
    await loadCatalog();
  }

  // ------------------------------------------------------------------
  // Catalog
  // ------------------------------------------------------------------

  Future<void> loadCatalog() async {
    _loading = true;
    notifyListeners();

    try {
      final res = await api.get(
        '/plugins/catalog',
        query: {'device_id': deviceId},
      );

      final catalog =
          CatalogResponse.fromJson(Map<String, dynamic>.from(res));

      _thisDevice = catalog.thisDevice;
      _thisDevicePlugins = catalog.thisDevicePlugins;
      _otherDeviceGroups = catalog.otherDeviceGroups;

      // Rebuild the flat map.
      _plugins.clear();
      _thisDeviceIds.clear();

      for (final p in _thisDevicePlugins) {
        _plugins[p.id] = p;
        _thisDeviceIds.add(p.id);
      }
      for (final g in _otherDeviceGroups) {
        for (final p in g.plugins) {
          _plugins.putIfAbsent(p.id, () => p);
        }
      }

      debugPrint(
        '[PluginManager] loadCatalog: '
        '${_thisDevicePlugins.length} for this device, '
        '${_otherDeviceGroups.length} other device group(s)',
      );
    } catch (e, st) {
      debugPrint('[PluginManager] loadCatalog failed: $e');
      debugPrintStack(stackTrace: st);
      rethrow;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }


  // ------------------------------------------------------------------
  // Local install / uninstall (this device only)
  // ------------------------------------------------------------------

  /// Install a plugin into the device running this app.
  ///
  /// Only valid for plugins returned by `thisDevicePlugins`. Calls
  /// `service.install()` locally (download, verify, persist) then
  /// reports to the backend.
  Future<void> install(String pluginId, String version) async {
    final plugin = _requireForThisDevice(pluginId);
    plugin.state = PluginState.installing;
    plugin.error = null;
    notifyListeners();

    try {
      await service.install(pluginId, version);

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

      plugin.state = PluginState.disabled;
      plugin.error = null;
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
    final plugin = _requireForThisDevice(pluginId);

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

      plugin.state = PluginState.available;
      plugin.error = null;
    } catch (e) {
      plugin.state = PluginState.error;
      plugin.error = e.toString();
      rethrow;
    } finally {
      notifyListeners();
    }
  }

  // ------------------------------------------------------------------
  // Enable / disable (this device only)
  // ------------------------------------------------------------------

  Future<void> enable(String pluginId) async {
    final plugin = _requireForThisDevice(pluginId);

    try {
      final ok = await service.enable(pluginId);
      if (!ok) {
        throw StateError('Native load failed for $pluginId');
      }

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
    final plugin = _requireForThisDevice(pluginId);

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
      plugin.error = null;
    } catch (e) {
      plugin.state = PluginState.error;
      plugin.error = e.toString();
      rethrow;
    } finally {
      notifyListeners();
    }
  }

  // ------------------------------------------------------------------
  // Reconcile
  // ------------------------------------------------------------------

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

  // ------------------------------------------------------------------
  // Capability configuration
  // ------------------------------------------------------------------

  /// Fetch the merged config for [pluginId] from the backend.
  /// The backend stores it in `plugin_metadata["capabilities"]`.
  Future<Map<String, Map<String, dynamic>>> loadCapabilityConfig(
    String pluginId,
  ) async {
    if (_capConfigCache.containsKey(pluginId)) {
      return _capConfigCache[pluginId]!;
    }
    try {
      final res = await api.get('/plugins/$pluginId/config');
      final cfg = res['config'];
      final caps = <String, Map<String, dynamic>>{};
      if (cfg is Map && cfg['capabilities'] is Map) {
        (cfg['capabilities'] as Map).forEach((k, v) {
          if (v is Map) {
            caps[k.toString()] = Map<String, dynamic>.from(v);
          }
        });
      }
      _capConfigCache[pluginId] = caps;
      return caps;
    } catch (e) {
      debugPrint(
        '[PluginManager] loadCapabilityConfig($pluginId) failed: $e',
      );
      _capConfigCache[pluginId] = {};
      return {};
    }
  }

  /// Persist capability config for [pluginId].
  Future<void> saveCapabilityConfig(
    String pluginId,
    Map<String, Map<String, dynamic>> caps,
  ) async {
    final existing = await _loadRawConfig(pluginId);
    final merged = {
      ...existing,
      'capabilities': caps,
    };
    await api.post(
      '/plugins/$pluginId/config',
      {'config': merged},
    );
    _capConfigCache[pluginId] = caps;
    debugPrint(
      '[PluginManager] saved capability config for $pluginId',
    );
  }

  Future<Map<String, dynamic>> _loadRawConfig(String pluginId) async {
    try {
      final res = await api.get('/plugins/$pluginId/config');
      final cfg = res['config'];
      return cfg is Map ? Map<String, dynamic>.from(cfg) : {};
    } catch (_) {
      return {};
    }
  }

  // ------------------------------------------------------------------
  // Command-result hooks (called by PaiApp)
  // ------------------------------------------------------------------

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
      plugin.error =
          message['error']?.toString() ?? 'Plugin operation failed';
      notifyListeners();
      return;
    }

    switch (operation) {
      case 'plugin.install':
        plugin.state = PluginState.disabled;
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
        // Plugin may belong to another device — drop from this-device
        // list but leave the flat map entry unless it also vanishes
        // from the other-device groups on next loadCatalog().
        _thisDeviceIds.remove(pluginId);
        _thisDevicePlugins =
            _thisDevicePlugins.where((p) => p.id != pluginId).toList();
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
    plugin.error =
        message['error']?.toString() ?? 'Plugin operation failed';
    notifyListeners();
  }

  // ------------------------------------------------------------------
  // Internal helpers
  // ------------------------------------------------------------------

  /// Lookup + assert the plugin is installable on *this* device.
  /// Used by install / uninstall / enable / disable — those operations
  /// only make sense locally. For other-device plugins, use
  /// `installOnDevice()`.
  PluginInfo _requireForThisDevice(String pluginId) {
    final p = _plugins[pluginId];
    if (p == null) {
      throw StateError('Plugin not found: $pluginId');
    }
    if (!_thisDeviceIds.contains(pluginId)) {
      throw StateError(
        'Plugin $pluginId is not installable on this device. '
        'Use installOnDevice() to install it remotely.',
      );
    }
    return p;
  }
}