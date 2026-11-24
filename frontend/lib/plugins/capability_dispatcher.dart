
import 'dart:io';

import 'package:flutter/foundation.dart';

import './models/plugin_info.dart';
import './plugin_installer.dart';
import './plugin_registry.dart';
import './runtime/plugin_runtime.dart';

class CapabilityDispatcher {
  final PluginRegistry registry;
  final PluginInstaller installer;
  final PluginRuntime runtime;

  /// ABI for the current device, e.g. arm64-v8a.
  ///
  /// Supply the actual ABI from your platform/runtime layer.
  final String abi;

  final Map<String, _ModuleInfo> _cache = {};

  CapabilityDispatcher({
    required this.registry,
    required this.installer,
    required this.runtime,
    required this.abi,
  });

  /// Call after installation, uninstallation, upgrade,
  /// or any change to native module metadata.
  void invalidateCache() {
    _cache.clear();
  }

  Future<Map<String, dynamic>> invoke({
    required String capability,
    required Map<String, dynamic> parameters,
  }) async {
    final info = await _resolve(capability);

    if (!await runtime.isModuleAvailable(info.nativeModule)) {
      debugPrint(
        '[CapabilityDispatcher] Loading ${info.nativeModule}',
      );

      final load = await runtime.loadNativeModule(
        nativeModule: info.nativeModule,
        artifactPath: info.artifactPath,
        entrypoint: info.entrypoint,
        capabilities: info.allCapabilities,
        permissions: info.permissions,
      );

      if (load['ok'] != true) {
        final denied = _asStringList(load['denied']);

        if (denied.isNotEmpty) {
          throw StateError(
            'Permissions denied for ${info.nativeModule}: '
            '${denied.join(", ")}. '
            'Grant them from the Plugins screen and try again.',
          );
        }

        throw StateError(
          'Failed to load native module ${info.nativeModule}: '
          '${load['error'] ?? 'unknown_error'}',
        );
      }
    }

    return runtime.invoke(
      nativeModule: info.nativeModule,
      capability: capability,
      parameters: parameters,
    );
  }

  Future<_ModuleInfo> _resolve(String capabilityId) async {
    // Always resolve against the current registry.
    // This prevents invoking capabilities of disabled/uninstalled plugins.
    final resolved = registry.resolveCapability(capabilityId);

    if (resolved == null) {
      throw StateError(
        'No enabled plugin provides capability "$capabilityId".',
      );
    }

    final plugin = resolved.plugin;
    final info = plugin.info;

    if (!plugin.enabled) {
      throw StateError(
        'Plugin ${plugin.id} is disabled.',
      );
    }

    if (!info.isDevice) {
      throw StateError(
        'Plugin ${info.id} is not a device runtime plugin.',
      );
    }

    // Check the cache only after verifying the current registry.
    final cached = _cache[capabilityId];

    if (cached != null &&
        cached.pluginId == plugin.id &&
        cached.version == plugin.version) {
      return cached;
    }

    final platform = _platformKey();

    final platformSpec = _resolvePlatformSpec(
      info.platformSpecs,
      platform,
    );

    if (platformSpec == null) {
      throw UnsupportedError(
        'Plugin ${info.id} does not support platform "$platform".',
      );
    }

    final nativeModule = info.runtimeSpec.nativeModule.trim();

    if (nativeModule.isEmpty) {
      throw StateError(
        'Plugin ${info.id} does not declare a native module.',
      );
    }

    final entrypoint = platformSpec.entrypoint.trim();

    if (entrypoint.isEmpty) {
      throw StateError(
        'Plugin ${info.id} does not declare an entrypoint '
        'for platform "$platform".',
      );
    }

    final artifact = await installer.resolveNativeArtifact(
      id: info.id,
      version: info.version,
      platform: platform,
      abi: abi,
      platformSpec: platformSpec,
    );

    // Use the full capability definitions from DevicePlugin to ensure
    // this capability really belongs to this installed plugin.
    final allCapabilities = plugin.capabilities
        .map((capability) => capability.id)
        .toList(growable: false);

    final moduleInfo = _ModuleInfo(
      pluginId: plugin.id,
      version: plugin.version,
      nativeModule: nativeModule,
      artifactPath: artifact.path,
      entrypoint: entrypoint,
      allCapabilities: allCapabilities,
      permissions: info.permissions,
    );

    _cache[capabilityId] = moduleInfo;

    return moduleInfo;
  }

  PlatformSpec? _resolvePlatformSpec(
    List<PlatformSpec> specs,
    String platform,
  ) {
    for (final spec in specs) {
      if (spec.platform == platform) {
        return spec;
      }
    }

    return null;
  }

  List<String> _asStringList(dynamic value) {
    if (value == null) return const [];

    if (value is List) {
      return value
          .map((item) => item.toString())
          .toList(growable: false);
    }

    if (value is String) {
      return value
          .split(',')
          .map((item) => item.trim())
          .where((item) => item.isNotEmpty)
          .toList(growable: false);
    }

    return const [];
  }

  String _platformKey() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isLinux) return 'linux';
    if (Platform.isWindows) return 'windows';
    if (Platform.isMacOS) return 'macos';
    if (Platform.isIOS) return 'ios';

    throw UnsupportedError('Unsupported platform');
  }
}

class _ModuleInfo {
  final String pluginId;
  final String version;
  final String nativeModule;
  final String artifactPath;
  final String entrypoint;
  final List<String> allCapabilities;
  final List<String> permissions;

  const _ModuleInfo({
    required this.pluginId,
    required this.version,
    required this.nativeModule,
    required this.artifactPath,
    required this.entrypoint,
    required this.allCapabilities,
    required this.permissions,
  });
}
