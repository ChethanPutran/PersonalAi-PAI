import 'dart:io';

import 'package:flutter/foundation.dart';

import 'device_plugin.dart';
import 'models/plugin_info.dart';
import 'plugin_command_router.dart';
import 'plugin_installer.dart';
import 'plugin_registry.dart';
import 'plugin_repository.dart';
import 'runtime/plugin_runtime.dart';

/// Internal façade used by the UI and the command router.
///
/// NOT the backend REST client.
class PluginService {
  final PluginInstaller installer;
  final PluginRepository repository;
  final PluginRuntime runtime;
  final PluginRegistry registry;

  PluginCommandRouter? _router;

  PluginService({
    required this.installer,
    required this.repository,
    required this.runtime,
    required this.registry,
  });

  void attachRouter(PluginCommandRouter router) {
    _router = router;
  }

  // ---------------------------------------------------------------
  // Catalog / install / lifecycle
  // ---------------------------------------------------------------

  Future<List<Map<String, dynamic>>> browse() async {
    final index = await repository.fetchIndex();
    return (index['plugins'] as List).cast<Map<String, dynamic>>();
  }

  /// Download and install a plugin version.
  Future<PluginInfo> install(String id, String version) async {
    final manifest = await installer.install(id, version);

    // Register it in-memory (disabled).
    registry.add(DevicePlugin(info: manifest, enabled: false));

    return manifest;
  }

  Future<Map<String, dynamic>> readInstalled() => installer.readInstalled();
Future<List<PluginInfo>> discoverLocal() => installer.discover();

  /// Load the native module and mark enabled.
  Future<bool> enable(String id) async {
    var plugin = registry.byId(id);

    // Not in memory yet? Try to load its manifest from disk.
    if (plugin == null) {
      final manifests = await installer.discover();
      final manifest = manifests.cast<PluginInfo?>().firstWhere(
            (m) => m?.id == id,
            orElse: () => null,
          );
      if (manifest == null) return false;
      plugin = DevicePlugin(info: manifest, enabled: false);
      registry.add(plugin);
    }

    final platform = _platformKey();
    final spec = plugin.info.platforms[platform];
    if (spec == null) return false;

    final path = await installer.artifactPath(
      plugin.info.id,
      plugin.info.version,
      platform,
      spec,
    );
    if (!File(path).existsSync()) return false;

    final ok = await runtime.loadNativeModule(
      nativeModule: plugin.info.nativeModule,
      artifactPath: path,
      entrypoint: spec.entrypoint,
      capabilities: plugin.info.capabilities,
      permissions: plugin.info.permissions,
    );

    if (!ok) return false;

    plugin.enabled = true;
    await installer.setEnabled(id, true);
    return true;
  }

  Future<void> disable(String id) async {
    final plugin = registry.byId(id);
    if (plugin == null) return;
    await runtime.unloadNativeModule(plugin.info.nativeModule);
    plugin.enabled = false;
    await installer.setEnabled(id, false);
  }

  Future<void> uninstall(String id) async {
    await disable(id);
    await installer.remove(id);
    registry.remove(id);
  }

  /// Dispatch a capability call.
  Future<Map<String, dynamic>> invoke(
    String capability,
    Map<String, dynamic> params,
  ) async {
    final resolved = registry.resolveCapability(capability);
    if (resolved == null) {
      return {
        'ok': false,
        'error': 'capability_not_available',
        'capability': capability,
      };
    }

    return runtime.invoke(
      nativeModule: resolved.plugin.info.nativeModule,
      capability: capability,
      parameters: params,
    );
  }

  /// Read the local plugin store and produce the payload the backend
  /// expects for reconciliation.
  Future<List<Map<String, dynamic>>> localSnapshot() async {
    final manifests = await installer.discover();
    final installed = await installer.readInstalled();

    final out = <Map<String, dynamic>>[];
    for (final info in manifests) {
      final entry = installed[info.id];
      final enabled = entry is Map && entry['enabled'] == true;
      out.add({
        'id': info.id,
        'version': info.version,
        'enabled': enabled,
      });
    }
    return out;
  }

  // ---------------------------------------------------------------
  // Startup
  // ---------------------------------------------------------------

  /// Read installed.json and hydrate the registry.
  /// Does NOT auto-enable — the user decides.
  Future<void> restoreInstalled() async {
  debugPrint('[PluginService] restoreInstalled: scanning disk');
  final manifests = await installer.discover();
  final installed = await installer.readInstalled();

  for (final m in manifests) {
    final entry = installed[m.id];
    final enabled = entry is Map && entry['enabled'] == true;

    registry.add(DevicePlugin(info: m, enabled: false));

    if (!enabled) {
      debugPrint('[PluginService]   ${m.id}@${m.version} installed (disabled)');
      continue;
    }

    debugPrint('[PluginService]   ${m.id}@${m.version} re-enabling');
    try {
      await enable(m.id);
      debugPrint('[PluginService]   → ${m.id} enabled OK');
    } catch (e) {
      debugPrint('[PluginService]   → ${m.id} enable FAILED: $e');
      // Leave it disabled in installed.json? Or keep as-is for next try?
      // For now: leave the flag alone so the user can retry manually.
    }
  }
}

  String _platformKey() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isLinux) return 'linux';
    if (Platform.isWindows) return 'windows';
    if (Platform.isMacOS) return 'macos';
    if (Platform.isIOS) return 'ios';
    throw UnsupportedError('unknown platform');
  }
}