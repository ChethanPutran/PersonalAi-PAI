import 'dart:io';

import 'package:flutter/foundation.dart';

import 'device_plugin.dart';
import 'models/plugin_info.dart';
import 'plugin_command_router.dart';
import 'plugin_installer.dart';
import 'plugin_registry.dart';
import 'plugin_repository.dart';
import 'runtime/plugin_runtime.dart';

/// Thrown by [PluginService.install] when the user denies one or more
/// of the permissions the plugin's native manifest declares. The
/// plugin has already been rolled back at this point — nothing on
/// disk, nothing in the registry — so the caller's only job is UX:
/// show [denied] to the user and offer to retry / open Settings.
class PluginPermissionDeniedException implements Exception {
  final String id;
  final List<String> denied;
  final List<String> permissions;

  PluginPermissionDeniedException({
    required this.id,
    required this.denied,
    required this.permissions,
  });

  @override
  String toString() =>
      'Plugin $id: permissions denied '
      '(${denied.length} of ${permissions.length}: ${denied.join(", ")})';
}

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

  /// Download, verify, load the native module (which triggers the OS
  /// permission dialog), and register. If the user denies any of the
  /// permissions the plugin's manifest declares, this rolls back —
  /// deletes the downloaded files, unloads the native module, and
  /// throws [PluginPermissionDeniedException] so the UI can show
  /// which permissions were refused.
  Future<PluginInfo> install(String id, String version) async {

    // 1. Detect ABI once
    final abi = await runtime.getAbi();
    debugPrint('[PluginService] install($id) abi=$abi');

    // 2. Download with ABI-aware URL
    final manifest = await installer.install(id, version, abi: abi);

    // ---- 2. Resolve the artifact path for this platform ----
    final platform = _platformKey();
    final spec = manifest.platforms[platform];
    if (spec == null) {
      await installer.remove(id);
      throw PluginRepositoryException(
        'plugin $id@$version has no artifact for platform $platform',
      );
    }
    final path = await installer.artifactPath(id, version, platform, spec,abi: abi);

    // ---- 3. Load the .so — Kotlin reads the declared permissions, -----
    //         shows the OS dialog(s), and returns the result.
    // Unload any prior registration first. Handles three cases that would
    // otherwise fail with `already_registered`:
    //   • user clicked Install twice
    //   • app hot-restarted (Dart state reset, native state not)
    //   • a prior uninstall didn't clear the native registry
    // No-op if nothing was registered.
    try {
      await runtime.unloadNativeModule(manifest.nativeModule);
    } catch (_) {}

    final result = await runtime.loadNativeModule(
      nativeModule: manifest.nativeModule,
      artifactPath: path,
      entrypoint: spec.entrypoint,
      capabilities: manifest.capabilities,
      permissions: manifest.permissions,
      type: manifest.type,
    );

    // ---- 4. Failure path — roll back cleanly ----
    if (result['ok'] != true) {
      // Unload even though Kotlin should have already done it. Belt
      // and suspenders — if the .so was loaded before the permission
      // check for any reason, this releases it.
      try {
        await runtime.unloadNativeModule(manifest.nativeModule);
      } catch (_) {}

      await installer.remove(id);

      final denied = List<String>.from(result['denied'] as List? ?? const []);
      if (denied.isNotEmpty) {
        throw PluginPermissionDeniedException(
          id: id,
          denied: denied,
          permissions: List<String>.from(
            result['permissions'] as List? ?? manifest.permissions,
          ),
        );
      }

      throw PluginRepositoryException(
        'loadNativeModule failed: ${result['error']}',
      );
    }

    // ---- 5. Success path — plugin is loaded and permitted ----
    await installer.setEnabled(id, true);
    registry.add(DevicePlugin(info: manifest, enabled: true));

    debugPrint('[PluginService] installed + enabled $id@$version');
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
    
    // NEW — pick up the current device ABI so artifactPath finds the
    // per-ABI file when it exists on disk.
    final abi = await runtime.getAbi();

    final path = await installer.artifactPath(
      plugin.info.id,
      plugin.info.version,
      platform,
      spec,
      abi: abi,   
    );
    if (!File(path).existsSync()) return false;

      try {
    await runtime.unloadNativeModule(plugin.info.nativeModule);
  } catch (_) {}

    final result = await runtime.loadNativeModule(
      nativeModule: plugin.info.nativeModule,
      artifactPath: path,
      entrypoint: spec.entrypoint,
      capabilities: plugin.info.capabilities,
      permissions: plugin.info.permissions,
      type: plugin.info.type,
    );

    if (result['ok'] != true) {
      debugPrint('[PluginService] enable($id) failed: $result');
      return false;
    }

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
  // Resolve the native module name even when the plugin isn't in the
  // Dart registry. Otherwise the C++ side keeps the module loaded and
  // the next install fails with `already_registered`.
  String? nativeName = registry.byId(id)?.info.nativeModule;
  if (nativeName == null) {
    try {
      final manifests = await installer.discover();
      for (final m in manifests) {
        if (m.id == id) {
          nativeName = m.nativeModule;
          break;
        }
      }
    } catch (_) {}
  }

  if (nativeName != null) {
    try {
      await runtime.unloadNativeModule(nativeName);
    } catch (_) {}
  }

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

    // Convert all params to strings, because the native side only accepts string values.
    // This is a temporary workaround until we implement proper type handling in the native module.
    final stringParams = params.map((k, v) => MapEntry(k, v?.toString() ?? ''));

    return runtime.invoke(
      nativeModule: resolved.plugin.info.nativeModule,
      capability: capability,
      parameters: stringParams,
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
      out.add({'id': info.id, 'version': info.version, 'enabled': enabled});
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
      final wasEnabled = entry is Map && entry['enabled'] == true;

      registry.add(DevicePlugin(info: m, enabled: false));

      if (!wasEnabled) {
        debugPrint(
          '[PluginService]   ${m.id}@${m.version} installed (disabled)',
        );
        continue;
      }

      debugPrint('[PluginService]   ${m.id}@${m.version} re-enabling');

      final ok = await enable(m.id);
      if (ok) {
        debugPrint('[PluginService]   → ${m.id} enabled OK');
      } else {
        // Common case: user revoked a permission in Settings since the
        // last run. Leave the plugin installed but disabled — the user
        // can re-enable from the Plugins screen, which will re-trigger
        // the OS permission dialog.
        debugPrint(
          '[PluginService]   → ${m.id} enable FAILED '
          '(likely permission revoked) — leaving disabled',
        );
        await installer.setEnabled(m.id, false);
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
