import 'dart:async';


import 'package:flutter/foundation.dart';
import 'package:pai_mobile/plugins/capability_dispatcher.dart';
import 'package:pai_mobile/plugins/models/permission_consent_handler.dart';

import '../core/api/api_client.dart';
import '../core/app_version.dart';
import 'models/catalog_response.dart';
import 'models/plugin_update.dart';
import 'models/plugin_info.dart';
import 'models/plugin_operation.dart';
import 'models/plugin_state.dart';
import 'permission_reporter.dart';
import 'plugin_service.dart';
import 'plugin_settings.dart';

// ---------------------------------------------------------------------------
// Remote operation tracking
// ---------------------------------------------------------------------------

enum RemotePhase {
  idle,
  installing,
  failed,
  succeeded,
}

/// Tracks the state of a remote plugin operation (what the UI shows).
class RemoteOp {
  final RemotePhase phase;

  /// Plain-language failure text (never a raw exception) - shown with [hint].
  final String? error;

  /// What to do about the failure.
  final String? hint;

  /// While installing: requested | delivered | downloading | verifying | installing |
  /// awaiting_permission (see [remoteStageLabel]).
  final String? stage;

  /// The backend operation this tracks.
  final String? operationId;

  const RemoteOp({
    this.phase = RemotePhase.idle,
    this.error,
    this.hint,
    this.stage,
    this.operationId,
  });

  String get stageLabel => remoteStageLabel(stage);
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

  /// Reports the user's device-side permission choices to the backend (advisory). Optional.
  final PermissionReporter? permissionReporter;

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

  /// Installed version of each locally installed plugin.
  final Map<String, String> _localVersions = {};

  /// Backend-computed update status: deviceId -> pluginId -> update.
  final Map<String, Map<String, PluginUpdate>> _updates = {};

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
    this.permissionReporter,
  });

  /// Reports, for every plugin installed here, the permissions its manifest declares and what the user
  /// decided. Safe to call anywhere: never throws, does nothing without a reporter.
  Future<void> reportPermissions({bool force = false}) async {
    final reporter = permissionReporter;
    if (reporter == null) return;
    try {
      final declared = <String, List<String>>{};
      final modules = <String, String>{};
      for (final id in _localInstalledIds) {
        final perms = _plugins[id]?.permissions ?? const <String>[];
        if (perms.isEmpty) continue;
        declared[id] = perms;
        final module = _plugins[id]?.runtimeSpec.nativeModule;
        if (module != null && module.isNotEmpty) modules[id] = module;
      }
      await reporter.report(declared, nativeModules: modules, force: force);
    } catch (e) {
      debugPrint('[PluginManager] permission report failed: $e');
    }
  }

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

  // -------------------------------------------------------------------------
  // Versions / updates
  // -------------------------------------------------------------------------

  /// Capability ids exposed by the version actually installed here (NOT the newest catalog
  /// version): what the plugin can do on this device right now.
  List<String>? installedCapabilities(String pluginId) =>
      service.registry.byId(pluginId)?.capabilities.map((c) => c.id).toList(growable: false);

  /// Version installed on this device, or null.
  String? installedVersion(String pluginId) => _localVersions[pluginId];

  /// Update status of an installed plugin (this device unless [deviceId] is given).
  PluginUpdate? updateFor(String pluginId, {String? deviceId}) =>
      _updates[deviceId ?? this.deviceId]?[pluginId];

  /// True only when a newer, *runnable* version exists. A newer version that is blocked by an
  /// old app/server does not count (see [PluginUpdate.blocked]).
  bool hasUpdate(String pluginId, {String? deviceId}) =>
      updateFor(pluginId, deviceId: deviceId)?.updateAvailable ?? false;

  /// Number of plugins with an update on this device.
  int get updateCount =>
      (_updates[deviceId]?.values ?? const <PluginUpdate>[]).where((u) => u.updateAvailable).length;

  /// Fetches update status for all of the user's devices. Never throws: updates are a
  /// convenience and must not break the plugin screen if the backend is older/unreachable.
  Future<void> loadUpdates() async {
    try {
      final res = await api.get('/plugins/updates');
      final devices = (res is Map ? res['devices'] : null);
      if (devices is! Map) return;
      _updates.clear();
      devices.forEach((did, v) {
        final plugins = (v is Map ? v['plugins'] : null) as List? ?? const [];
        _updates[did.toString()] = {
          for (final p in plugins.whereType<Map>())
            (p['plugin_id']?.toString() ?? ''):
                PluginUpdate.fromJson(Map<String, dynamic>.from(p)),
        };
      });
      notifyListeners();
    } catch (e) {
      debugPrint('[PluginManager] loadUpdates failed (ignored): $e');
    }
  }

  /// Updates an installed plugin to the newest version this device can run.
  Future<void> update(String pluginId) async {
    final u = updateFor(pluginId);
    final target = u?.latestVersion;
    if (u == null || !u.updateAvailable || target == null) {
      throw StateError('No update available for $pluginId');
    }
    await install(pluginId, target);
    await loadUpdates();
  }

  /// Updates a plugin on another of the user's devices.
  Future<void> updateOnDevice(String pluginId, String targetDeviceId) async {
    final u = updateFor(pluginId, deviceId: targetDeviceId);
    final target = u?.latestVersion;
    if (u == null || !u.updateAvailable || target == null) {
      throw StateError('No update available for $pluginId on $targetDeviceId');
    }
    await installOnDevice(pluginId, target, targetDeviceId);
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
  DateTime? _catalogLoadedAt;

  /// True when the last reconcile changed what the backend knows about this device (so the catalog is stale).
  bool lastReconcileChangedBackend = false;

  /// [maxAge]: skip the request when the catalog was loaded that recently (startup loads it, then the
  /// reconcile after the socket connects would load the very same data again).
  Future<void> loadCatalog({Duration? maxAge}) async {
    final at = _catalogLoadedAt;
    if (maxAge != null && at != null && DateTime.now().difference(at) < maxAge) {
      debugPrint('[PluginManager] Catalog is fresh; not reloading');
      return;
    }
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
      await loadUpdates();
      // Other devices' installed state comes from the backend too (they may never answer a sync).
      await Future.wait([
        for (final group in _otherDeviceGroups) refreshDevicePlugins(group.deviceId),
      ]);

      debugPrint(
        '[PluginManager] Catalog loaded: '
        '${_thisDevicePlugins.length} for this device, '
        '${_otherDeviceGroups.length} other device group(s)',
      );
      _catalogLoadedAt = DateTime.now();
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
    _localVersions.clear();

    for (final item in snapshot) {
      final id = item['id']?.toString();

      if (id == null || id.isEmpty) continue;

      installedIds.add(id);
      final ver = item['version']?.toString();
      if (ver != null && ver.isNotEmpty) _localVersions[id] = ver;

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
  Future<void> install(String pluginId, String version, {Map<String, dynamic>? config}) async {
    final info = _requireForThisDevice(pluginId);

    // Client-side gate (the server checks too): never install what this app can't run.
    final blocked = info.compatible
        ? appIncompatibility(info.runtimeSpec.minAppVersion)
        : info.incompatibleReason;
    if (blocked != null) throw StateError(blocked);

    // Settings chosen in the install dialog: saved on the server first (it validates them and enforces them
    // from now on), then handed to the plugin together with its first load.
    Map<String, dynamic>? effectiveConfig;
    if (config != null && config.isNotEmpty) {
      final saved = await saveSettings(deviceId, pluginId, config, applyLocally: false);
      effectiveConfig = saved.effective;
    }

    _pluginStates[pluginId] = PluginState.installing;
    _pluginErrors[pluginId] = null;
    notifyListeners();

    try {
      await service.install(
        pluginId,
        version,
        onPermissionConsent: onPermissionConsent,
        config: effectiveConfig,
      );

      // PluginService enables the plugin when installation succeeds.
      _localInstalledIds.add(pluginId);
      _localVersions[pluginId] = version;
      _updates[deviceId]?.remove(pluginId);
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

      // The app now shows the plugin as enabled, so the server must agree (see _enableOnBackend).
      if (!await _enableOnBackend(pluginId, deviceId)) {
        _pluginErrors[pluginId] =
            'Installed, but the server could not enable it. Turn "Enable" off and on to retry.';
      }
    } on PluginPermissionPendingException catch (e) {
      // The package was installed successfully, but activation is pending.
      _localInstalledIds.add(pluginId);
      _localVersions[pluginId] = version;
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
      _localVersions[pluginId] = version;
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
      await reportPermissions();
    }
  }

  /// Uninstalls a plugin from this device.
  Future<void> uninstall(String pluginId) async {
    _requireForThisDevice(pluginId);

    try {
      await service.uninstall(pluginId);

      _localInstalledIds.remove(pluginId);
      _localVersions.remove(pluginId);
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
      await reportPermissions();
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

      await _enableOnBackend(pluginId, deviceId);
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
      await reportPermissions();
    }
  }

  /// Tells the backend this plugin is enabled for the account (and on [targetDeviceId]).
  ///
  /// Installing makes the app SHOW a plugin as enabled, but only this call makes the server's router and
  /// authorization treat it as enabled. Without it every action fails with "'x' is provided by [...] but none
  /// are enabled for your account. Enable plugin first". Returns false (and logs) if the server refused.
  Future<bool> _enableOnBackend(String pluginId, String targetDeviceId) async {
    try {
      await api.post('/plugins/$pluginId/enable', {'device_id': targetDeviceId});
      return true;
    } catch (e) {
      debugPrint('[PluginManager] backend enable failed: $e');
      return false;
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
      await reportPermissions();
    }
  }

  // -------------------------------------------------------------------------
  // Remote install
  // -------------------------------------------------------------------------

  /// Requests installation of a plugin on another device.
  ///
  /// The backend answers at once with an operation; its progress then arrives as `plugin.operation`
  /// frames on the device WebSocket (see [handleOperationFrame]), so nothing polls. Rejections the
  /// user can act on (remote install off on that device, ...) are shown in plain words.
  Future<void> installOnDevice(
    String pluginId,
    String version,
    String targetDeviceId, {
    Map<String, dynamic>? config,
  }) async {
    final key = _remoteKey(targetDeviceId, pluginId);
    if (config != null) _remoteConfigs[key] = config;           // a Retry sends the same choices

    _remoteOps[key] = const RemoteOp(
      phase: RemotePhase.installing,
      stage: 'requested',
    );

    notifyListeners();

    try {
      final response = await api.post('/plugins/$pluginId/install', {
        'device_id': targetDeviceId,
        'version': version,
        if (config != null && config.isNotEmpty) 'config': config,
      });

      // The push frame may already have arrived; applying the same operation twice is harmless.
      final op = PluginOperation.tryParse(response is Map ? response['operation'] : null);
      if (op != null) handleOperation(op);
    } catch (e) {
      // The server refused a setting value: not an install failure. Back to idle, the dialog shows the reason.
      if (e is ApiException && e.statusCode == 422) {
        final detail = e.body is Map ? (e.body as Map)['detail'] : null;
        if (detail is Map && detail['code'] == 'invalid_plugin_config') {
          _remoteOps.remove(key);
          notifyListeners();
          throw PluginSettingsException.fromApi(e);
        }
      }
      final text = describeRemoteRequestError(e is ApiException ? e.body : null);
      _remoteOps[key] = RemoteOp(
        phase: RemotePhase.failed,
        error: e is ApiException ? text.message : "Couldn't reach the server. Check your connection and try again.",
        hint: e is ApiException ? text.hint : null,
      );

      notifyListeners();
      rethrow;
    }
  }

  /// The settings sent with the last install request per (device, plugin), reused by Retry.
  final Map<String, Map<String, dynamic>> _remoteConfigs = {};

  /// Operations whose success was already handled (the frame can repeat, e.g. after a reconnect).
  final Set<String> _settledOps = {};

  /// Applies a `plugin.operation` WebSocket frame (`{type, operation: {...}}`).
  void handleOperationFrame(Map<String, dynamic> frame) {
    final op = PluginOperation.tryParse(frame['operation']);
    if (op != null) handleOperation(op);
  }

  /// Moves the remote-install state for (device, plugin) to what the backend reports.
  /// Only installs on OTHER devices are tracked here; a finished operation never goes back.
  void handleOperation(PluginOperation op) {
    if (op.kind != 'install' || op.deviceId == deviceId) return;
    final key = _remoteKey(op.deviceId, op.pluginId);
    if (stageHold == Duration.zero) {
      _applyOperation(op);
      return;
    }
    // A cached install passes every stage in milliseconds; show each one for at least [stageHold]
    // so the user can see what is happening. Stages are applied in order, one per hold.
    final queue = _heldOps.putIfAbsent(key, () => <PluginOperation>[]);
    final last = _lastApplied[key];
    if (queue.isEmpty && (last == null || DateTime.now().difference(last) >= stageHold)) {
      _lastApplied[key] = DateTime.now();
      _applyOperation(op);
      return;
    }
    queue.add(op);
    _heldTimers[key] ??= Timer(_remainingHold(key), () => _drainHeld(key));
  }

  /// How long each stage stays on screen before the next one replaces it.
  Duration stageHold = const Duration(milliseconds: 500);
  final Map<String, List<PluginOperation>> _heldOps = {};
  final Map<String, DateTime> _lastApplied = {};
  final Map<String, Timer> _heldTimers = {};

  Duration _remainingHold(String key) {
    final last = _lastApplied[key];
    if (last == null) return Duration.zero;
    final left = stageHold - DateTime.now().difference(last);
    return left.isNegative ? Duration.zero : left;
  }

  void _drainHeld(String key) {
    _heldTimers.remove(key);
    final queue = _heldOps[key];
    if (queue == null || queue.isEmpty) return;
    _lastApplied[key] = DateTime.now();
    _applyOperation(queue.removeAt(0));
    if (queue.isNotEmpty) _heldTimers[key] = Timer(stageHold, () => _drainHeld(key));
  }

  void _applyOperation(PluginOperation op) {
    final key = _remoteKey(op.deviceId, op.pluginId);
    final current = _remoteOps[key];

    // A finished operation is final, and an older operation never overwrites a newer one.
    if (current?.operationId == op.id && current!.phase != RemotePhase.installing) return;

    if (op.failed) {
      _remoteOps[key] = RemoteOp(
        phase: RemotePhase.failed,
        operationId: op.id,
        error: op.errorMessage ?? "The device couldn't finish the installation.",
        hint: op.errorHint,
      );
    } else if (op.succeeded) {
      _remoteOps[key] = RemoteOp(phase: RemotePhase.succeeded, operationId: op.id);
      if (_settledOps.add(op.id)) unawaited(_afterRemoteInstall(op));
    } else {
      _remoteOps[key] = RemoteOp(phase: RemotePhase.installing, stage: op.status, operationId: op.id);
    }
    notifyListeners();
  }

  Future<void> _afterRemoteInstall(PluginOperation op) async {
    try {
      // The other device enabled it locally on install; make the server agree (see _enableOnBackend).
      await _enableOnBackend(op.pluginId, op.deviceId);
      // The remote device's own state (what the "Install" button reads) comes from the backend.
      await refreshDevicePlugins(op.deviceId);
      // Refresh catalog after the remote installation completes.
      await loadCatalog();
    } catch (e) {
      debugPrint('[PluginManager] post-install refresh failed: $e');
    }
  }

  /// First load / reconnect: pick up operations that are still running (their frames may have been
  /// missed while the socket was down).
  Future<void> loadActiveOperations() async {
    try {
      final response = await api.get('/plugins/operations', query: {'active': 'true'});
      final list = response is Map ? response['operations'] : null;
      if (list is! List) return;
      for (final raw in list) {
        final op = PluginOperation.tryParse(raw);
        if (op != null) handleOperation(op);
      }
    } catch (e) {
      debugPrint('[PluginManager] loading active operations failed: $e');
    }
  }

  // -------------------------------------------------------------------------
  // Results of install/uninstall COMMANDS this device ran for someone else
  // -------------------------------------------------------------------------

  /// Tells the backend how a requested install ended on THIS device (the final result of the
  /// operation; the stages go over the WebSocket as `plugin.progress`).
  Future<void> reportCommandInstall({
    required String pluginId,
    required String? version,
    String? operationId,
    required bool success,
    String? error,
    bool enabled = true,
  }) async {
    try {
      await api.post('/plugins/$pluginId/report-install', {
        'device_id': deviceId,
        'operation_id': operationId,
        'version': version ?? '',
        'success': success,
        if (!enabled) 'enabled': false,
        if (error != null) 'error': error,
      });
    } catch (e) {
      debugPrint('[PluginManager] report-install failed: $e');
    }
  }

  Future<void> reportCommandUninstall({
    required String pluginId,
    String? operationId,
    required bool success,
    String? error,
  }) async {
    try {
      await api.post('/plugins/$pluginId/report-uninstall', {
        'device_id': deviceId,
        'operation_id': operationId,
        'success': success,
        if (error != null) 'error': error,
      });
    } catch (e) {
      debugPrint('[PluginManager] report-uninstall failed: $e');
    }
  }

  /// Retries a failed remote installation.
  Future<void> retryInstallOnDevice(
    String pluginId,
    String version,
    String targetDeviceId, {
    Map<String, dynamic>? config,
  }) async {
    _remoteOps.remove(
      _remoteKey(targetDeviceId, pluginId),
    );

    notifyListeners();

    await installOnDevice(
      pluginId,
      version,
      targetDeviceId,
      config: config ?? _remoteConfigs[_remoteKey(targetDeviceId, pluginId)],
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
  // Plugin settings (manifest configSchema), per device
  // -------------------------------------------------------------------------

  /// Whether a plugin on a device has settings, as far as we have asked the server: null = not asked yet.
  final Map<String, bool> _settingsKnown = {};
  bool? hasSettingsKnown(String targetDeviceId, String pluginId) => _settingsKnown[_remoteKey(targetDeviceId, pluginId)];

  /// The schema, saved values, defaults and effective values of [pluginId] on [targetDeviceId]
  /// (this device or another one the user owns). Throws [PluginSettingsException].
  Future<PluginSettings> loadSettings(String targetDeviceId, String pluginId) async {
    try {
      final r = await api.get('/plugins/device/$targetDeviceId/$pluginId/config');
      final settings = PluginSettings.fromJson(r);
      final key = _remoteKey(targetDeviceId, pluginId);
      if (_settingsKnown[key] != settings.hasSettings) {
        _settingsKnown[key] = settings.hasSettings;
        notifyListeners();
      }
      return settings;
    } catch (e) {
      throw PluginSettingsException.fromApi(e);
    }
  }

  /// Saves [values] (null resets a key) for [pluginId] on [targetDeviceId]. For THIS device the plugin gets
  /// the new effective values at once; another device is told by the server (and needs its remote install
  /// switch on: the 403 comes back as a [PluginSettingsException] with `remoteOff`).
  Future<PluginSettings> saveSettings(
    String targetDeviceId,
    String pluginId,
    Map<String, dynamic> values, {
    bool applyLocally = true,
  }) async {
    try {
      final r = await api.put('/plugins/device/$targetDeviceId/$pluginId/config', {'values': values});
      final settings = PluginSettings.fromJson(r);
      if (applyLocally && targetDeviceId == deviceId) {
        await service.applyConfig(pluginId, settings.effective);
      }
      return settings;
    } catch (e) {
      throw PluginSettingsException.fromApi(e);
    }
  }

  /// A `plugin.config` frame for this device (the caller already applied the remote-access rule).
  Future<void> applyConfigFrame(String pluginId, Map<String, dynamic> effective) async {
    await service.applyConfig(pluginId, effective);
    notifyListeners();
  }

  /// After (re)connecting: the settings may have changed while the socket was down, so ask the server for
  /// every installed plugin and hand the plugin what it says. Plugins without settings answer an empty
  /// schema and are skipped. Never throws.
  Future<void> syncSettingsFromServer() async {
    for (final pluginId in _localInstalledIds.toList()) {
      try {
        final s = await loadSettings(deviceId, pluginId);
        if (s.hasSettings) await service.applyConfig(pluginId, s.effective);
      } catch (e) {
        debugPrint('[PluginManager] settings sync for $pluginId skipped: $e');
      }
    }
  }

  /// Whether [pluginId] declares settings (asks the server; false on any failure).
  Future<bool> hasSettings(String targetDeviceId, String pluginId) async {
    try {
      return (await loadSettings(targetDeviceId, pluginId)).hasSettings;
    } catch (_) {
      return false;
    }
  }

  // -------------------------------------------------------------------------
  // Reconciliation
  // -------------------------------------------------------------------------

  /// Sends the local plugin snapshot to the backend.
  ///
  /// The backend can use this to reconcile its view of device plugin state.
  /// Returns true if the backend accepted the report (false: it failed, retry later).
  Future<bool> reconcileWithBackend() async {
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

      bool nonEmpty(Object? v) => v is Iterable && v.isNotEmpty;
      lastReconcileChangedBackend = nonEmpty(response['added']) || nonEmpty(response['updated']) || nonEmpty(response['removed']);
      await _refreshLocalPluginStates();
      notifyListeners();
      await reportPermissions(force: true);   // a reconcile may follow a server restart
      return true;
    } catch (e, st) {
      debugPrint('[PluginManager] reconcile failed: $e');
      debugPrintStack(stackTrace: st);
      return false;
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
          installed && (item['enabled'] == true || item['enabled_on_device'] == true);

      states[pluginId] = DevicePluginState(
        installed: installed,
        enabled: enabled,
      );
    }

    _devicePluginStates[targetDeviceId] = states;

    notifyListeners();
  }

  /// Fetches what is installed/enabled on another of the user's devices from the backend.
  /// Needed because servers/desktops without a socket to this app never send a sync response.
  Future<void> refreshDevicePlugins(String targetDeviceId) async {
    if (targetDeviceId == deviceId) return;
    try {
      final response = await api.get('/plugins/device/$targetDeviceId/installed');
      final list = response is Map ? response['plugins'] : null;
      if (list is List) updateDevicePluginStates(targetDeviceId, list);
    } catch (e) {
      debugPrint('[PluginManager] refreshing device plugins failed: $e');
    }
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
    for (final t in _heldTimers.values) {
      t.cancel();
    }
    _heldTimers.clear();
    _heldOps.clear();
    _plugins.clear();
    _thisDeviceIds.clear();
    _pluginStates.clear();
    _pluginErrors.clear();
    _localInstalledIds.clear();
    _localVersions.clear();
    _updates.clear();
    _remoteOps.clear();
    _settledOps.clear();
    _capConfigCache.clear();
    _devicePluginStates.clear();

    super.dispose();
  }
}
