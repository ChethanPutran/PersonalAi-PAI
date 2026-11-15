import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import './models/plugin_info.dart';
import './plugin_installer.dart';
import './plugin_manager.dart';
import './runtime/plugin_runtime.dart';

/// Resolves a capability id (e.g. `camera.capture`) to the native module
/// declared by an installed plugin and invokes it through [PluginRuntime].
///
/// One instance per app session; results are cached so repeated invokes
/// don't re-read manifests from disk.
class CapabilityDispatcher {
  final PluginManager pluginManager;
  final PluginInstaller installer;
  final PluginRuntime runtime;

  final Map<String, _ModuleInfo> _cache = {};

  CapabilityDispatcher({
    required this.pluginManager,
    required this.installer,
    required this.runtime,
  });

  /// Call this when plugins are installed / uninstalled / disabled so the
  /// capability → module mapping stays correct.
  void invalidateCache() => _cache.clear();

  Future<Map<String, dynamic>> invoke({
  required String capability,
  required Map<String, dynamic> parameters,
}) async {
  final info = await _resolve(capability);

  if (!await runtime.isModuleAvailable(info.nativeModule)) {
    debugPrint('[Dispatcher] loading ${info.nativeModule}');
    final load = await runtime.loadNativeModule(
      nativeModule: info.nativeModule,
      artifactPath: info.artifactPath,
      entrypoint: info.entrypoint,
      capabilities: info.allCapabilities,
      permissions: info.permissions,
    );

    if (load['ok'] != true) {
      // Surface permission denial distinctly from other failures, so
      // the chat UI can say "grant camera permission" rather than
      // a generic "load failed".
      final denied = _asStringList(load['denied']);
      if (denied.isNotEmpty) {
        throw Exception(
          'Permissions denied for ${info.nativeModule}: '
          '${denied.join(", ")}. '
          'Grant them from the Plugins screen and try again.',
        );
      }
      throw Exception(
        'Failed to load native module ${info.nativeModule}: '
        '${load['error'] ?? 'unknown_error'}',
      );
    }
  }

  return await runtime.invoke(
    nativeModule: info.nativeModule,
    capability: capability,
    parameters: parameters,
  );
}

/// Android sends `List<String>`; Linux sends a comma-separated `String`.
/// Normalize both to a list.
List<String> _asStringList(dynamic v) {
  if (v == null) return const [];
  if (v is List) return v.map((e) => e.toString()).toList();
  if (v is String) {
    if (v.isEmpty) return const [];
    return v.split(',').map((s) => s.trim()).where((s) => s.isNotEmpty).toList();
  }
  return const [];
}

  Future<_ModuleInfo> _resolve(String capability) async {
    final cached = _cache[capability];
    if (cached != null) return cached;

    for (final plugin in pluginManager.plugins) {
      if (!plugin.isInstalled) continue;
      if (!_pluginProvides(plugin, capability)) continue;

      final dir = await installer.pluginDir(plugin.id, plugin.version);
      final manifestFile = File('${dir.path}/manifest.json');
      if (!await manifestFile.exists()) continue;

      final manifest =
          jsonDecode(await manifestFile.readAsString()) as Map<String, dynamic>;
      final runtimeMap =
          (manifest['runtime'] as Map?)?.cast<String, dynamic>();
      final nativeModule = runtimeMap?['nativeModule']?.toString();
      if (nativeModule == null || nativeModule.isEmpty) continue;

      final entrypoint =
          runtimeMap?['entrypoint']?.toString() ?? 'pai_plugin_module_create';

      final platform = _platformKey();
      final platforms = manifest['platforms'] as Map?;
      final spec = platforms?[platform] as Map?;
      final artifactName = spec?['artifact']?.toString();
      if (artifactName == null || artifactName.isEmpty) continue;

      final info = _ModuleInfo(
        nativeModule: nativeModule,
        artifactPath: '${dir.path}/$artifactName',
        entrypoint: entrypoint,
        allCapabilities: plugin.capabilityDetails
            .map((c) => c.id)
            .toList(growable: false),
        permissions: (manifest['permissions'] as List?)
                ?.map((e) => e.toString())
                .toList(growable: false) ??
            const [],
      );
      _cache[capability] = info;
      return info;
    }

    throw Exception('No installed plugin provides "$capability"');
  }

  bool _pluginProvides(PluginInfo plugin, String capability) {
    // capabilityDetails is the canonical source.
    if (plugin.capabilityDetails.any((c) => c.id == capability)) return true;
    // Fallback for older PluginInfo versions that only have ids.
    return plugin.capabilities.any((c) => c == capability);
  }

  String _platformKey() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isLinux) return 'linux';
    if (Platform.isWindows) return 'windows';
    if (Platform.isMacOS) return 'macos';
    if (Platform.isIOS) return 'ios';
    throw Exception('Unsupported platform');
  }
}

class _ModuleInfo {
  final String nativeModule;
  final String artifactPath;
  final String entrypoint;
  final List<String> allCapabilities;
  final List<String> permissions;

  _ModuleInfo({
    required this.nativeModule,
    required this.artifactPath,
    required this.entrypoint,
    required this.allCapabilities,
    required this.permissions,
  });
}