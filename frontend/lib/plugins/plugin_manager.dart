
import 'package:flutter/foundation.dart';
import 'package:pai_mobile/plugins/capability_dispatcher.dart';
import 'package:pai_mobile/plugins/models/permission_consent_handler.dart';

import '../core/api/api_client.dart';
import 'models/catalog_response.dart';
import 'models/plugin_info.dart';
import 'models/plugin_state.dart';
import 'plugin_service.dart';

// ---------------------------------------------------------------------------
// Remote operation tracking
// ---------------------------------------------------------------------------

enum RemotePhase {
  idle,
  installing,
  failed,
  succeeded,
}

/// Tracks the state of a remote plugin operation.
class RemoteOp {
  final RemotePhase phase;
  final String? error;

  const RemoteOp({
    this.phase = RemotePhase.idle,
    this.error,
  });
}

// ---------------------------------------------------------------------------
// Device plugin state
// ---------------------------------------------------------------------------

/// Represents a plugin's installation state on another device.
class DevicePluginState {
  final bool installed;
  final bool enabled;

  const DevicePluginState({
    required this.installed,
    required this.enabled,
  });
}

// ---------------------------------------------------------------------------
// PluginManager
// ---------------------------------------------------------------------------

/// Coordinates plugin catalog metadata, local plugin operations, remote
/// plugin operations, UI state, and capability configuration.
///
/// PluginService performs actual local installation and native execution.
/// PluginManager exposes state and operations to the application UI.
class PluginManager extends ChangeNotifier {
  final PluginService service;
  final ApiClient api;
  final String deviceId;
  final CapabilityDispatcher dispatcher;

  /// Optional callback used to ask the user for permission consent.
  ///
  /// The runtime remains responsible for checking and requesting actual
  /// operating-system permissions.
  final PermissionConsentHandler? onPermissionConsent;

  /// Plugin installation states reported by other devices.
  ///
  /// Shape:
  /// {
  ///   deviceId: {
  ///     pluginId: DevicePluginState(...)
  ///   }
  /// }
  final Map<String, Map<String, DevicePluginState>> _devicePluginStates = {};

  /// Catalog metadata indexed by plugin ID.
  final Map<String, PluginInfo> _plugins = {};

  /// IDs of plugins compatible with this device.
  final Set<String> _thisDeviceIds = {};

  List<PluginInfo> _thisDevicePlugins = [];
  List<OtherDeviceGroup> _otherDeviceGroups = [];

  bool _loading = false;

  // -------------------------------------------------------------------------
  // Device-local UI state
  // -------------------------------------------------------------------------

  /// UI state for plugins installed on this device.
  ///
  /// The persisted installation state is restored from PluginService.
  final Map<String, PluginState> _pluginStates = {};

  /// Last known error for each plugin.
  final Map<String, String?> _pluginErrors = {};

  /// IDs known to be installed locally.
  final Set<String> _localInstalledIds = {};

  // -------------------------------------------------------------------------
  // Remote operation state
  // -------------------------------------------------------------------------

  /// Tracks remote operations using a device/plugin composite key.
  final Map<String, RemoteOp> _remoteOps = {};

  // -------------------------------------------------------------------------
  // Capability configuration cache
  // -------------------------------------------------------------------------

  /// Shape:
  /// {
  ///   pluginId: {
  ///     capabilityId: {
  ///       'enabled': bool,
  ///       'params': {...}
  ///     }
  ///   }
  /// }
  final Map<String, Map<String, Map<String, dynamic>>> _capConfigCache = {};

  PluginManager({
    required this.service,
    required this.api,
    required this.deviceId,
    required this.dispatcher,
    this.onPermissionConsent,
  });

  // -------------------------------------------------------------------------
  // Getters
  // -------------------------------------------------------------------------

  /// Flat union of this-device and other-device catalog plugins.
  List<PluginInfo> get plugins => List.unmodifiable(_plugins.values);

  List<PluginInfo> get thisDevicePlugins =>
      List.unmodifiable(_thisDevicePlugins);

  List<OtherDeviceGroup> get otherDeviceGroups =>
      List.unmodifiable(_otherDeviceGroups);

  bool get isLoading => _loading;

  PluginInfo? get(String id) => _plugins[id];

  bool contains(String id) => _plugins.containsKey(id);

  bool isForThisDevice(String id) => _thisDeviceIds.contains(id);

  /// Returns the UI state of a local plugin.
  PluginState stateFor(String pluginId) {
    return _pluginStates[pluginId] ?? PluginState.available;
  }

  String? errorFor(String pluginId) {
    return _pluginErrors[pluginId];
  }

  bool isInstalled(String pluginId) {
    return _localInstalledIds.contains(pluginId);
  }

  bool isEnabled(String pluginId) {
    return stateFor(pluginId) == PluginState.enabled;
  }

  bool isInstalledOnDevice(
    String pluginId,
    String targetDeviceId,
  ) {
    if (targetDeviceId == deviceId) {
      return isInstalled(pluginId);
    }

    return _devicePluginStates[targetDeviceId]?[pluginId]?.installed ?? false;
  }

  bool isEnabledOnDevice(
    String pluginId,
    String targetDeviceId,
  ) {
    if (targetDeviceId == deviceId) {
      return isEnabled(pluginId);
    }

    return _devicePluginStates[targetDeviceId]?[pluginId]?.enabled ?? false;
  }

  RemoteOp remoteStateFor(
    String targetDeviceId,
    String pluginId,
  ) {
    return _remoteOps[_remoteKey(targetDeviceId, pluginId)] ??
        const RemoteOp();
  }

  String _remoteKey(String targetDeviceId, String pluginId) {
    return '$targetDeviceId::$pluginId';
  }

  // -------------------------------------------------------------------------
  // Catalog
  // -------------------------------------------------------------------------

  /// Loads the plugin catalog from the backend and refreshes local state.
  Future<void> loadCatalog() async {
    _loading = true;
    notifyListeners();

    try {
      final response = await api.get('/plugins/catalog');

      final catalog = CatalogResponse.fromJson(
        Map<String, dynamic>.from(response),
        deviceId,
      );

      _thisDevicePlugins = catalog.thisDevicePlugins;
      _otherDeviceGroups = catalog.otherDeviceGroups;

      _plugins.clear();
      _thisDeviceIds.clear();

      // Register plugins compatible with this device.
      for (final plugin in _thisDevicePlugins) {
        _plugins[plugin.id] = plugin;
        _thisDeviceIds.add(plugin.id);

        _pluginStates.putIfAbsent(
          plugin.id,
          () => PluginState.available,
        );
      }

      // Add plugins available on other devices without overwriting
      // metadata already registered for this device.
      for (final group in _otherDeviceGroups) {
        for (final plugin in group.plugins) {
          _plugins.putIfAbsent(plugin.id, () => plugin);
        }
      }

      // The backend catalog is not the source of truth for local installation.
      await _refreshLocalPluginStates();

      debugPrint(
        '[PluginManager] Catalog loaded: '
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

  /// Rebuilds local installation state from persisted installation metadata.
  Future<void> _refreshLocalPluginStates() async {
    final snapshot = await service.localSnapshot();

    final installedIds = <String>{};

    for (final item in snapshot) {
      final id = item['id']?.toString();

      if (id == null || id.isEmpty) continue;

      installedIds.add(id);

      final enabled = item['enabled'] == true;

      _pluginStates[id] =
          enabled ? PluginState.enabled : PluginState.disabled;

      _pluginErrors[id] = null;
    }

    // Replace the local installed-ID set with the actual disk snapshot.
    _localInstalledIds
      ..clear()
      ..addAll(installedIds);

    // Plugins that were installed previously but are no longer installed
    // should return to the available state.
    for (final id in _pluginStates.keys.toList()) {
      if (!installedIds.contains(id) && _thisDeviceIds.contains(id)) {
        if (_pluginStates[id] == PluginState.enabled ||
            _pluginStates[id] == PluginState.disabled ||
            _pluginStates[id] == PluginState.installed) {
          _pluginStates[id] = PluginState.available;
          _pluginErrors[id] = null;
        }
      }
    }
  }

  // -------------------------------------------------------------------------
  // Local install / uninstall
  // -------------------------------------------------------------------------

  /// Installs a plugin locally.
  ///
  /// PluginService installs and enables the plugin on success.
  /// If permission consent is pending, the package remains installed but
  /// disabled and the manager reflects that state.
  Future<void> install(String pluginId, String version) async {
    _requireForThisDevice(pluginId);

    _pluginStates[pluginId] = PluginState.installing;
    _pluginErrors[pluginId] = null;
    notifyListeners();

    try {
      await service.install(
        pluginId,
        version,
        onPermissionConsent: onPermissionConsent,
      );

      // PluginService enables the plugin when installation succeeds.
      _localInstalledIds.add(pluginId);
      _pluginStates[pluginId] = PluginState.enabled;
      _pluginErrors[pluginId] = null;

      try {
        await api.post('/plugins/$pluginId/report-install', {
          'device_id': deviceId,
          'version': version,
          'success': true,
        });
      } catch (e) {
        debugPrint(
          '[PluginManager] report-install failed: $e',
        );
      }
    } on PluginPermissionPendingException catch (e) {
      // The package was installed successfully, but activation is pending.
      _localInstalledIds.add(pluginId);
      _pluginStates[pluginId] = PluginState.disabled;
      _pluginErrors[pluginId] = e.toString();

      try {
        await api.post('/plugins/$pluginId/report-install', {
          'device_id': deviceId,
          'version': version,
          'success': true,
          'enabled': false,
          'permission_pending': true,
          'error': e.toString(),
        });
      } catch (reportError) {
        debugPrint(
          '[PluginManager] pending install report failed: $reportError',
        );
      }

      rethrow;
    } on PluginPermissionDeniedException catch (e) {
      // The package remains installed but cannot be activated.
      _localInstalledIds.add(pluginId);
      _pluginStates[pluginId] = PluginState.disabled;
      _pluginErrors[pluginId] = e.toString();

      try {
        await api.post('/plugins/$pluginId/report-install', {
          'device_id': deviceId,
          'version': version,
          'success': true,
          'enabled': false,
          'error': e.toString(),
        });
      } catch (reportError) {
        debugPrint(
          '[PluginManager] denied install report failed: $reportError',
        );
      }

      rethrow;
    } catch (e) {
      _pluginStates[pluginId] = PluginState.error;
      _pluginErrors[pluginId] = e.toString();

      try {
        await api.post('/plugins/$pluginId/report-install', {
          'device_id': deviceId,
          'version': version,
          'success': false,
          'error': e.toString(),
        });
      } catch (_) {}

      rethrow;
    } finally {
      notifyListeners();
    }
  }

  /// Uninstalls a plugin from this device.
  Future<void> uninstall(String pluginId) async {
    _requireForThisDevice(pluginId);

    try {
      await service.uninstall(pluginId);

      _localInstalledIds.remove(pluginId);
      _pluginStates[pluginId] = PluginState.available;
      _pluginErrors[pluginId] = null;

      // Cached capability configuration is reloaded after reinstall.
      _capConfigCache.remove(pluginId);

      try {
        await api.post('/plugins/$pluginId/report-uninstall', {
          'device_id': deviceId,
          'success': true,
        });
      } catch (e) {
        debugPrint(
          '[PluginManager] report-uninstall failed: $e',
        );
      }
    } catch (e) {
      _pluginStates[pluginId] = PluginState.error;
      _pluginErrors[pluginId] = e.toString();
      rethrow;
    } finally {
      notifyListeners();
    }
  }

  // -------------------------------------------------------------------------
  // Local enable / disable
  // -------------------------------------------------------------------------

  /// Enables a plugin already installed on this device.
  Future<void> enable(String pluginId) async {
    _requireForThisDevice(pluginId);

    if (!isInstalled(pluginId)) {
      throw StateError(
        'Plugin $pluginId is not installed on this device.',
      );
    }

    try {
      final success = await service.enable(
        pluginId,
        onPermissionConsent: onPermissionConsent,
      );

      if (!success) {
        throw StateError(
          'Native load failed for plugin $pluginId.',
        );
      }

      _pluginStates[pluginId] = PluginState.enabled;
      _pluginErrors[pluginId] = null;

      try {
        await api.post('/plugins/$pluginId/enable', {
          'device_id': deviceId,
        });
      } catch (e) {
        debugPrint(
          '[PluginManager] backend enable failed: $e',
        );
      }
    } on PluginPermissionPendingException catch (e) {
      // The plugin is still installed, but remains disabled.
      _pluginStates[pluginId] = PluginState.disabled;
      _pluginErrors[pluginId] = e.toString();
      rethrow;
    } on PluginPermissionDeniedException catch (e) {
      _pluginStates[pluginId] = PluginState.disabled;
      _pluginErrors[pluginId] = e.toString();
      rethrow;
    } catch (e) {
      _pluginStates[pluginId] = PluginState.error;
      _pluginErrors[pluginId] = e.toString();
      rethrow;
    } finally {
      notifyListeners();
    }
  }

  /// Disables a plugin installed on this device.
  Future<void> disable(String pluginId) async {
    _requireForThisDevice(pluginId);

    if (!isInstalled(pluginId)) {
      throw StateError(
        'Plugin $pluginId is not installed on this device.',
      );
    }

    try {
      await service.disable(pluginId);

      _pluginStates[pluginId] = PluginState.disabled;
      _pluginErrors[pluginId] = null;

      try {
        await api.post('/plugins/$pluginId/disable', {
          'device_id': deviceId,
        });
      } catch (e) {
        debugPrint(
          '[PluginManager] backend disable failed: $e',
        );
      }
    } catch (e) {
      _pluginStates[pluginId] = PluginState.error;
      _pluginErrors[pluginId] = e.toString();
      rethrow;
    } finally {
      notifyListeners();
    }
  }

  // -------------------------------------------------------------------------
  // Remote install
  // -------------------------------------------------------------------------

  /// Requests installation of a plugin on another device.
  ///
  /// The actual installation runs on the target device. This method tracks
  /// the remote operation and polls the backend for its result.
  Future<void> installOnDevice(
    String pluginId,
    String version,
    String targetDeviceId,
  ) async {
    final key = _remoteKey(targetDeviceId, pluginId);

    _remoteOps[key] = const RemoteOp(
      phase: RemotePhase.installing,
    );

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

    // Poll asynchronously so the UI does not wait for the full operation.
    _pollRemoteInstall(
      targetDeviceId,
      pluginId,
      version,
    );
  }

  /// Polls backend state until the remote installation succeeds, fails,
  /// or times out.
  Future<void> _pollRemoteInstall(
    String targetDeviceId,
    String pluginId,
    String expectedVersion, {
    Duration interval = const Duration(seconds: 2),
    Duration timeout = const Duration(minutes: 5),
  }) async {
    final key = _remoteKey(targetDeviceId, pluginId);
    final deadline = DateTime.now().add(timeout);

    while (DateTime.now().isBefore(deadline)) {
      await Future.delayed(interval);

      try {
        final response = await api.get(
          '/plugins/device/$targetDeviceId/installed',
        );

        final entries = response['plugins'] as List? ?? [];

        final matchingEntries = entries.where(
          (item) =>
              item is Map &&
              item['plugin_id']?.toString() == pluginId,
        );

        if (matchingEntries.isEmpty) continue;

        final entry = matchingEntries.first as Map;

        final error = entry['last_error']?.toString();
        final installed = entry['is_installed'] == true;
        final installedVersion = entry['version']?.toString();

        if (error != null &&
            error.isNotEmpty &&
            error != 'install_requested') {
          _remoteOps[key] = RemoteOp(
            phase: RemotePhase.failed,
            error: error,
          );

          notifyListeners();
          return;
        }

        if (installed && installedVersion == expectedVersion) {
          _remoteOps[key] = const RemoteOp(
            phase: RemotePhase.succeeded,
          );

          notifyListeners();

          // Refresh catalog after the remote installation completes.
          await loadCatalog();
          return;
        }
      } catch (e) {
        debugPrint(
          '[PluginManager] remote poll error: $e',
        );
      }
    }

    _remoteOps[key] = RemoteOp(
      phase: RemotePhase.failed,
      error:
          'Timed out after ${timeout.inMinutes} minutes. '
          'Check the device agent log.',
    );

    notifyListeners();
  }

  /// Retries a failed remote installation.
  Future<void> retryInstallOnDevice(
    String pluginId,
    String version,
    String targetDeviceId,
  ) async {
    _remoteOps.remove(
      _remoteKey(targetDeviceId, pluginId),
    );

    notifyListeners();

    await installOnDevice(
      pluginId,
      version,
      targetDeviceId,
    );
  }

  // -------------------------------------------------------------------------
  // Remote enable / disable
  // -------------------------------------------------------------------------

  /// Requests that a plugin be enabled on another device.
  Future<void> enableOnDevice(
    String pluginId,
    String targetDeviceId,
  ) async {
    await api.post(
      '/plugins/$pluginId/enable',
      {'device_id': targetDeviceId},
    );

    await loadCatalog();
  }

  /// Requests that a plugin be disabled on another device.
  Future<void> disableOnDevice(
    String pluginId,
    String targetDeviceId,
  ) async {
    await api.post(
      '/plugins/$pluginId/disable',
      {'device_id': targetDeviceId},
    );

    await loadCatalog();
  }

  // -------------------------------------------------------------------------
  // Reconciliation
  // -------------------------------------------------------------------------

  /// Sends the local plugin snapshot to the backend.
  ///
  /// The backend can use this to reconcile its view of device plugin state.
  Future<void> reconcileWithBackend() async {
    debugPrint('[PluginManager] Reconciling with backend...');

    try {
      final snapshot = await service.localSnapshot();

      debugPrint(
        '[PluginManager] Local snapshot: '
        '${snapshot.map(
          (p) => "${p['id']}@${p['version']} enabled=${p['enabled']}",
        ).join(", ")}',
      );

      final response = await api.post(
        '/plugins/device/$deviceId/reconcile',
        {'plugins': snapshot},
      );

      debugPrint(
        '[PluginManager] Reconcile response: '
        'added=${response['added']} '
        'updated=${response['updated']} '
        'removed=${response['removed']}',
      );

      await _refreshLocalPluginStates();
      notifyListeners();
    } catch (e, st) {
      debugPrint('[PluginManager] reconcile failed: $e');
      debugPrintStack(stackTrace: st);
    }
  }

  // -------------------------------------------------------------------------
  // Capability configuration
  // -------------------------------------------------------------------------

  /// Loads capability configuration from cache or backend.
  Future<Map<String, Map<String, dynamic>>> loadCapabilityConfig(
    String pluginId,
  ) async {
    final cached = _capConfigCache[pluginId];

    if (cached != null) {
      return cached;
    }

    try {
      final response = await api.get(
        '/plugins/$pluginId/config',
      );

      final config = response['config'];
      final capabilities = <String, Map<String, dynamic>>{};

      if (config is Map && config['capabilities'] is Map) {
        (config['capabilities'] as Map).forEach((key, value) {
          if (value is Map) {
            capabilities[key.toString()] =
                Map<String, dynamic>.from(value);
          }
        });
      }

      _capConfigCache[pluginId] = capabilities;

      return capabilities;
    } catch (e) {
      debugPrint(
        '[PluginManager] loadCapabilityConfig($pluginId) failed: $e',
      );

      _capConfigCache[pluginId] = {};

      return {};
    }
  }

  /// Merges and saves capability configuration to the backend.
  Future<void> saveCapabilityConfig(
    String pluginId,
    Map<String, Map<String, dynamic>> capabilities,
  ) async {
    final existing = await _loadRawConfig(pluginId);

    final merged = {
      ...existing,
      'capabilities': capabilities,
    };

    await api.post(
      '/plugins/$pluginId/config',
      {'config': merged},
    );

    _capConfigCache[pluginId] = capabilities;

    debugPrint(
      '[PluginManager] Saved capability config for $pluginId',
    );

    notifyListeners();
  }

  /// Reads the complete raw configuration object from the backend.
  Future<Map<String, dynamic>> _loadRawConfig(
    String pluginId,
  ) async {
    try {
      final response = await api.get(
        '/plugins/$pluginId/config',
      );

      final config = response['config'];

      return config is Map
          ? Map<String, dynamic>.from(config)
          : {};
    } catch (_) {
      return {};
    }
  }

  // -------------------------------------------------------------------------
  // Command-result hooks
  // -------------------------------------------------------------------------

  /// Updates local UI state from a completed plugin command.
  ///
  /// Results from other devices must not modify this device's local state.
  void handleCommandResult(Map<String, dynamic> message) {
    final success = message['success'] == true;
    final result = message['result'];

    if (result is! Map) return;

    final operation = result['operation']?.toString();
    final pluginId = result['plugin_id']?.toString();

    if (pluginId == null) return;

    final targetDeviceId = result['device_id']?.toString();

    if (targetDeviceId != null &&
        targetDeviceId != deviceId) {
      return;
    }

    if (!_plugins.containsKey(pluginId)) return;

    if (!success) {
      _pluginStates[pluginId] = PluginState.error;
      _pluginErrors[pluginId] =
          message['error']?.toString() ??
          'Plugin operation failed';

      notifyListeners();
      return;
    }

    switch (operation) {
      case 'plugin.install':
        _localInstalledIds.add(pluginId);
        _pluginStates[pluginId] = PluginState.disabled;
        _pluginErrors[pluginId] = null;
        break;

      case 'plugin.enable':
        _pluginStates[pluginId] = PluginState.enabled;
        _pluginErrors[pluginId] = null;
        break;

      case 'plugin.disable':
        _pluginStates[pluginId] = PluginState.disabled;
        _pluginErrors[pluginId] = null;
        break;

      case 'plugin.uninstall':
        _localInstalledIds.remove(pluginId);
        _pluginStates[pluginId] = PluginState.available;
        _pluginErrors[pluginId] = null;
        break;
    }

    notifyListeners();
  }

  /// Updates local UI state when a plugin command fails.
  void handleCommandFailure(Map<String, dynamic> message) {
    final result = message['result'];

    if (result is! Map) return;

    final pluginId = result['plugin_id']?.toString();

    if (pluginId == null ||
        !_plugins.containsKey(pluginId)) {
      return;
    }

    final targetDeviceId = result['device_id']?.toString();

    if (targetDeviceId != null &&
        targetDeviceId != deviceId) {
      return;
    }

    _pluginStates[pluginId] = PluginState.error;
    _pluginErrors[pluginId] =
        message['error']?.toString() ??
        'Plugin operation failed';

    notifyListeners();
  }

  // -------------------------------------------------------------------------
  // Remote device state updates
  // -------------------------------------------------------------------------

  /// Replaces the known plugin states for a remote device.
  ///
  /// Accepts either `id` or `plugin_id` as the plugin identifier.
  /// Accepts either `is_installed` or `installed` as the installation flag.
  void updateDevicePluginStates(
    String targetDeviceId,
    List<dynamic> plugins,
  ) {
    final states = <String, DevicePluginState>{};

    for (final item in plugins) {
      if (item is! Map) continue;

      final pluginId =
          item['id']?.toString() ??
          item['plugin_id']?.toString();

      if (pluginId == null || pluginId.isEmpty) {
        continue;
      }

      final installed =
          item['is_installed'] == true ||
          item['installed'] == true;

      final enabled =
          installed && item['enabled'] == true;

      states[pluginId] = DevicePluginState(
        installed: installed,
        enabled: enabled,
      );
    }

    _devicePluginStates[targetDeviceId] = states;

    notifyListeners();
  }

  /// Removes all known plugin states for a disconnected remote device.
  void removeDevicePluginStates(String targetDeviceId) {
    _devicePluginStates.remove(targetDeviceId);
    notifyListeners();
  }

  // -------------------------------------------------------------------------
  // Internal helpers
  // -------------------------------------------------------------------------

  /// Returns plugin catalog metadata and verifies that it is installable
  /// on this device.
  PluginInfo _requireForThisDevice(String pluginId) {
    final plugin = _plugins[pluginId];

    if (plugin == null) {
      throw StateError('Plugin not found: $pluginId');
    }

    if (!_thisDeviceIds.contains(pluginId)) {
      throw StateError(
        'Plugin $pluginId is not installable on this device. '
        'Use installOnDevice() to install it remotely.',
      );
    }

    return plugin;
  }

  // -------------------------------------------------------------------------
  // Lifecycle
  // -------------------------------------------------------------------------

  @override
  void dispose() {
    _plugins.clear();
    _thisDeviceIds.clear();
    _pluginStates.clear();
    _pluginErrors.clear();
    _localInstalledIds.clear();
    _remoteOps.clear();
    _capConfigCache.clear();
    _devicePluginStates.clear();

    super.dispose();
  }
}
