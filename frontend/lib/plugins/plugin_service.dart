
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:pai_mobile/plugins/models/plugin_manifest.dart';
import 'package:pai_mobile/plugins/runtime/plugin_runtime.dart';

import 'models/device_plugin.dart';
import 'models/permission_consent_handler.dart';
import 'models/plugin_info.dart';
import 'models/plugin_state.dart';
import 'plugin_installer.dart';
import 'plugin_settings.dart';
import 'plugin_registry.dart';
import 'plugin_repository.dart';

/// Thrown when one or more OS permissions have been explicitly denied.
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
      '(${denied.length} of ${permissions.length}: '
      '${denied.join(", ")})';
}

/// Thrown when permission consent is still required.
///
/// The plugin package remains installed, but the plugin remains disabled.
class PluginPermissionPendingException implements Exception {
  final String id;
  final String nativeModule;
  final List<String> pending;
  final List<String> permissions;

  PluginPermissionPendingException({
    required this.id,
    required this.nativeModule,
    required this.pending,
    required this.permissions,
  });

  @override
  String toString() =>
      'Plugin $id: awaiting consent for ${pending.join(", ")}';
}

/// Coordinates plugin installation, native runtime loading, permissions,
/// capability invocation, and restoration of installed plugins.
///
/// PluginInstaller owns package storage and installation metadata.
/// PluginRuntime owns native module loading and execution.
/// PluginRegistry owns the in-memory collection of installed plugins.
class PluginService {
  final PluginInstaller installer;
  final PluginRepository repository;
  final PluginRuntime runtime;
  final PluginRegistry registry;

  /// The plugin settings (effective values) chosen for this device; handed to a plugin whenever it loads.
  final PluginConfigStore configStore;

  PluginService({
    required this.installer,
    required this.repository,
    required this.runtime,
    required this.registry,
    PluginConfigStore? configStore,
  }) : configStore = configStore ?? PluginConfigStore();

  /// Remembers [config] for [id] and pushes it to the plugin if it is loaded now. Returns whether a loaded
  /// plugin took it (otherwise it applies the next time the plugin loads).
  Future<bool> applyConfig(String id, Map<String, dynamic> config) async {
    await configStore.save(id, config);
    final nativeModule = registry.byId(id)?.info.runtimeSpec.nativeModule;
    if (nativeModule == null || nativeModule.isEmpty) return false;
    return runtime.configurePlugin(nativeModule, config);
  }

  // ---------------------------------------------------------------------------
  // Install
  // ---------------------------------------------------------------------------

  /// Downloads and installs a plugin, then loads its native module.
  ///
  /// Successful installation also enables the plugin.
  ///
  /// If consent is pending, the package remains installed but disabled.
  /// If installation or loading fails, the previous installed version is
  /// restored where possible.
  Future<PluginInfo> install(
    String id,
    String version, {
    PermissionConsentHandler? onPermissionConsent,
    Map<String, dynamic>? config,
  }) async {
    final abi = await runtime.getAbi();

    // Settings chosen in the install dialog (or sent with a remote install) apply from the first load.
    if (config != null) await configStore.save(id, config);

    debugPrint('[PluginService] install($id@$version), ABI=$abi');

    // Preserve the currently installed metadata and in-memory plugin.
    final previousInstalled = await installer.readInstalled();
    final previousEntry = previousInstalled[id];
    final previousPlugin = registry.byId(id);

    // Download, verify, extract, and persist the package.
    final pluginInfo = await installer.install(
      id,
      version,
      abi: abi,
    );

    final nativeModule = pluginInfo.runtimeSpec.nativeModule;

    try {
      // Read the full manifest. Catalog metadata alone does not contain
      // complete capability definitions.
      final manifest = await installer.readManifest(id, version);
      final capabilities = manifest.capabilityDefinitions;

      debugPrint(
        '[PluginService] Loaded ${capabilities.length} capability '
        'definitions for $id: '
        '${capabilities.map((c) => c.id).toList()}',
      );

      // Resolve the current platform's artifact declaration.
      final platform = _platformKey();
      final spec = _resolvePlatformSpec(pluginInfo, platform);

      if (spec == null) {
        throw PluginRepositoryException(
          'Plugin $id@$version does not support platform $platform',
        );
      }

      // Resolve the artifact path from the installed package.
      final artifactFile = await installer.resolveNativeArtifact(
        id: id,
        version: version,
        platform: platform,
        abi: abi,
        platformSpec: spec,
      );

      if (!await artifactFile.exists()) {
        throw PluginRepositoryException(
          'Plugin artifact does not exist: ${artifactFile.path}',
        );
      }

      // Remove an older native registration before loading this version.
      await _unloadSafely(nativeModule);

      // Load the native module. If the runtime reports pending permissions,
      // optionally ask the application to obtain user consent and retry.
      final result = await _loadNativeModuleWithConsent(
        id: id,
        pluginName: pluginInfo.name,
        nativeModule: nativeModule,
        artifactPath: artifactFile.path,
        entrypoint: spec.entrypoint,
        capabilities: capabilities.map((c) => c.id).toList(),
        permissions: pluginInfo.permissions,
        type: pluginInfo.type.name,
        onPermissionConsent: onPermissionConsent,
      );

      // Handle denied permissions and other native loading failures.
      if (result['ok'] != true) {
        final denied = _asStringList(result['denied']);

        final permissions = _asStringList(result['permissions'])
            .whenEmpty(pluginInfo.permissions);

        await _unloadSafely(nativeModule);

        if (denied.isNotEmpty) {
          throw PluginPermissionDeniedException(
            id: id,
            denied: denied,
            permissions: permissions,
          );
        }

        throw PluginRepositoryException(
          'loadNativeModule failed: ${result['error']}',
        );
      }

      // Persist the enabled state before publishing it in the registry.
      await installer.setEnabled(id, true);

      registry.add(
        DevicePlugin(
          info: pluginInfo,
          capabilities: capabilities,
          state: PluginState.enabled,
        ),
      );

      debugPrint('[PluginService] installed and enabled $id@$version');

      return pluginInfo;
    } on PluginPermissionPendingException {
  // Package is valid and installed. Activation is only *pending*.
  await _unloadSafely(nativeModule);
  await installer.setEnabled(id, false);

  final manifest = await installer.readManifest(id, version);
  registry.add(
    DevicePlugin(
      info: manifest.info,
      capabilities: manifest.capabilityDefinitions,
      state: PluginState.disabled,
    ),
  );

  rethrow;
} on PluginPermissionDeniedException {
  // NEW: package is valid; the user just said "no" to activation.
  // Keep it on disk but disabled so a later Enable (which re-prompts,
  // because we deliberately do NOT persist the deny) can succeed
  // without re-downloading.
  await _unloadSafely(nativeModule);
  await installer.setEnabled(id, false);

  try {
    final manifest = await installer.readManifest(id, version);
    registry.add(
      DevicePlugin(
        info: manifest.info,
        capabilities: manifest.capabilityDefinitions,
        state: PluginState.disabled,
      ),
    );
  } catch (_) {
    // If the manifest can't be read back, the package is unusable —
    // fall through to the rollback path below.
    await _rollbackInstall(
      id,
      version,
      previousEntry,
      previousPlugin: previousPlugin,
    );
  }

  rethrow;
} catch (e) {
  // Real failure (checksum, load, network, etc.) — roll back.
  await _unloadSafely(nativeModule);

  await _rollbackInstall(
    id,
    version,
    previousEntry,
    previousPlugin: previousPlugin,
  );

  rethrow;
}
  }

  // ---------------------------------------------------------------------------
  // Capability invocation
  // ---------------------------------------------------------------------------

  /// Executes a registered capability through the native runtime.
  ///
  /// Resolves the capability to its owning plugin using PluginRegistry,
  /// verifies that the plugin is enabled, and invokes the native module.
  ///
  /// Returns a structured result:
  /// {
  ///   "ok": true/false,
  ///   "capability": "...",
  ///   "plugin_id": "...",
  ///   "result": ...
  /// }
  ///
  /// Authorization and policy enforcement must happen before execution.
  Future<Map<String, dynamic>> invoke(
    String capability,
    Map<String, dynamic> params,
  ) async {
    debugPrint('[PluginService] invoke($capability)');

    // Resolve the capability to its owning plugin and definition.
    final resolved = registry.resolveCapability(capability);

    if (resolved == null) {
      debugPrint(
        '[PluginService] Capability not found or plugin disabled: $capability',
      );

      return {
        'ok': false,
        'capability': capability,
        'error': 'Capability not found or plugin is disabled: $capability',
      };
    }

    final plugin = resolved.plugin;
    final capabilityDefinition = resolved.capability;

    // Verify plugin state.
    if (!plugin.enabled) {
      return {
        'ok': false,
        'capability': capability,
        'plugin_id': plugin.id,
        'error': 'Plugin is disabled: ${plugin.id}',
      };
    }

    // Resolve the native module from the owning plugin.
    final nativeModule = plugin.info.runtimeSpec.nativeModule;

    debugPrint(
      '[PluginService] Executing capability=$capability '
      'plugin=${plugin.id} module=$nativeModule',
    );

    try {
      // Invoke the native runtime using its actual named-argument API.
      final runtimeResult = await runtime.invoke(
        nativeModule: nativeModule,
        capability: capabilityDefinition.id,
        parameters: params,
      );

      // PluginRuntime currently returns "ok" as either a bool or a string
      // on different execution paths. Normalize it here.
      final rawOk = runtimeResult['ok'];
      final success = rawOk == true || rawOk?.toString() == 'true';

      return {
        ...runtimeResult,
        'ok': success,
        'capability': capability,
        'plugin_id': plugin.id,
      };
    } catch (e, stackTrace) {
      debugPrint('[PluginService] invoke($capability) failed: $e');
      debugPrintStack(stackTrace: stackTrace);

      return {
        'ok': false,
        'capability': capability,
        'plugin_id': plugin.id,
        'error': e.toString(),
      };
    }
  }
  // ---------------------------------------------------------------------------
  // Enable
  // ---------------------------------------------------------------------------

  /// Enables a previously installed plugin.
  ///
  /// If the plugin is not in memory, its manifest is recovered from disk.
  /// The optional consent callback is used when the runtime reports pending
  /// permissions.
  Future<bool> enable(
    String pluginId, {
    PermissionConsentHandler? onPermissionConsent,
  }) async {
    // Confirm which version is currently installed.
    final installed = await installer.readInstalled();
    final entry = installed[pluginId];

    if (entry is! Map || entry['version'] == null) {
      debugPrint('[PluginService] $pluginId is not installed');
      return false;
    }

    final installedVersion = entry['version'].toString();

    // Reuse the registry entry only if it represents the installed version
    // and contains the complete capability definitions.
    var plugin = registry.byId(pluginId);

    if (plugin == null ||
        plugin.info.version != installedVersion ||
        plugin.capabilities.isEmpty) {
      final manifests = await installer.discover();

      PluginManifest? manifest;

      for (final candidate in manifests) {
        if (candidate.id == pluginId &&
            candidate.version == installedVersion) {
          manifest = candidate;
          break;
        }
      }

      if (manifest == null) {
        debugPrint(
          '[PluginService] Manifest not found for '
          '$pluginId@$installedVersion',
        );
        return false;
      }

      plugin = DevicePlugin(
        info: manifest.info,
        capabilities: manifest.capabilityDefinitions,
        state: PluginState.disabled,
      );

      registry.add(plugin);
    }

    final info = plugin.info;
    final platform = _platformKey();
    final spec = _resolvePlatformSpec(info, platform);

    if (spec == null) {
      debugPrint('[PluginService] $pluginId does not support $platform');
      return false;
    }

    final abi = await runtime.getAbi();

    // Resolve and verify the installed native artifact.
    final artifactFile = await installer.resolveNativeArtifact(
      id: pluginId,
      version: installedVersion,
      platform: platform,
      abi: abi,
      platformSpec: spec,
    );

    if (!await artifactFile.exists()) {
      debugPrint('[PluginService] Artifact missing: ${artifactFile.path}');
      return false;
    }

    final nativeModule = info.runtimeSpec.nativeModule;

    // Avoid duplicate native registrations.
    await _unloadSafely(nativeModule);

    try {
      final result = await _loadNativeModuleWithConsent(
        id: pluginId,
        pluginName: info.name,
        nativeModule: nativeModule,
        artifactPath: artifactFile.path,
        entrypoint: spec.entrypoint,
        capabilities: plugin.capabilities.map((c) => c.id).toList(),
        permissions: info.permissions,
        type: info.type.name,
        onPermissionConsent: onPermissionConsent,
      );

      if (result['ok'] != true) {
        await _unloadSafely(nativeModule);

        final denied = _asStringList(result['denied']);

        if (denied.isNotEmpty) {
          throw PluginPermissionDeniedException(
            id: pluginId,
            denied: denied,
            permissions: _asStringList(result['permissions'])
                .whenEmpty(info.permissions),
          );
        }

        debugPrint(
          '[PluginService] enable($pluginId) failed: $result',
        );

        return false;
      }

      // Persist first. If persistence fails, unload the native module.
      try {
        await installer.setEnabled(pluginId, true);
      } catch (_) {
        await _unloadSafely(nativeModule);
        rethrow;
      }

      plugin.enabled = true;

      debugPrint('[PluginService] enabled $pluginId');

      return true;
    } catch (_) {
      // Never leave a partially loaded module registered.
      await _unloadSafely(nativeModule);
      plugin.enabled = false;
      rethrow;
    }
  }

  // ---------------------------------------------------------------------------
  // Disable
  // ---------------------------------------------------------------------------

  /// Unloads a plugin's native module and persists its disabled state.
  Future<void> disable(String id) async {
    final plugin = registry.byId(id);

    if (plugin == null) return;

    final nativeModule = plugin.info.runtimeSpec.nativeModule;

    // Unload first so the in-memory state reflects actual runtime state.
    await runtime.unloadNativeModule(nativeModule);

    plugin.enabled = false;

    await installer.setEnabled(id, false);

    debugPrint('[PluginService] disabled $id');
  }

  // ---------------------------------------------------------------------------
  // Uninstall
  // ---------------------------------------------------------------------------

  /// Unloads and removes a plugin from disk and the in-memory registry.
  Future<void> uninstall(String id) async {
    String? nativeModule =
        registry.byId(id)?.info.runtimeSpec.nativeModule;

    // The plugin may not be present in the in-memory registry after restart.
    if (nativeModule == null) {
      try {
        for (final manifest in await installer.discover()) {
          if (manifest.id == id) {
            nativeModule = manifest.info.runtimeSpec.nativeModule;
            break;
          }
        }
      } catch (e) {
        debugPrint(
          '[PluginService] Could not resolve native module: $e',
        );
      }
    }

    if (nativeModule != null) {
      await _unloadSafely(nativeModule);
    }

    // The plugin's permission decisions go with it: a reinstall must ask again, and a different plugin
    // installed later never inherits what the user allowed this one.
    if (nativeModule != null) {
      await runtime.forgetPermissions(nativeModule);
    }

    await installer.remove(id);
    registry.remove(id);

    debugPrint('[PluginService] uninstalled $id');
  }

  // ---------------------------------------------------------------------------
  // Catalog / local state
  // ---------------------------------------------------------------------------

  /// Returns the persisted installation index.
  Future<Map<String, dynamic>> readInstalled() {
    return installer.readInstalled();
  }

  /// Returns a snapshot of locally installed plugins for backend reconciliation.
  Future<List<Map<String, dynamic>>> localSnapshot() async {
    final manifests = await installer.discover();
    final installed = await installer.readInstalled();

    final result = <Map<String, dynamic>>[];

    for (final manifest in manifests) {
      final info = manifest.info;
      final entry = installed[info.id];

      if (entry is! Map || entry['version'] != info.version) {
        continue;
      }

      result.add({
        'id': info.id,
        'version': info.version,
        'enabled': entry['enabled'] == true,
      });
    }

    return result;
  }

  // ---------------------------------------------------------------------------
  // Startup restoration
  // ---------------------------------------------------------------------------

  /// Reconstructs the in-memory registry and reloads plugins that were enabled
  /// before the application stopped.
  ///
  /// Plugins with pending permissions or loading failures remain installed
  /// but disabled.
  Future<void> restoreInstalled() async {
    debugPrint('[PluginService] restoreInstalled: scanning disk');

    final manifests = await installer.discover();
    final installed = await installer.readInstalled();

    for (final manifest in manifests) {
      final info = manifest.info;
      final entry = installed[info.id];

      if (entry is! Map || entry['version'] != info.version) {
        continue;
      }

      final plugin = DevicePlugin(
        info: info,
        capabilities: manifest.capabilityDefinitions,
        state: PluginState.disabled,
      );

      registry.add(plugin);

      if (entry['enabled'] != true) {
        debugPrint(
          '[PluginService] ${info.id}@${info.version} installed disabled '
          'with ${plugin.capabilities.length} capabilities',
        );
        continue;
      }

      try {
        final ok = await enable(info.id);

        if (!ok) {
          plugin.enabled = false;
          await installer.setEnabled(info.id, false);

          debugPrint(
            '[PluginService] Could not re-enable ${info.id}; '
            'leaving disabled',
          );
        }
      } on PluginPermissionPendingException catch (e) {
        plugin.enabled = false;
        await installer.setEnabled(info.id, false);

        debugPrint(
          '[PluginService] Permissions pending for '
          '${info.id}: ${e.pending}',
        );
      } catch (e) {
        plugin.enabled = false;
        await installer.setEnabled(info.id, false);

        debugPrint(
          '[PluginService] Failed to restore ${info.id}: $e',
        );
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Native loading and permission consent
  // ---------------------------------------------------------------------------

   /// Loads a native module and handles permission consent and OS requests.
  ///
  /// Flow:
  /// 1. Attempt to load the native module.
  /// 2. If permissions are pending, ask the user for consent.
  /// 3. If accepted, request the actual OS permissions through MethodChannel.
  /// 4. If permissions are granted, retry native module loading.
  /// 5. If denied or still pending, leave the plugin disabled.
  Future<Map<String, dynamic>> _loadNativeModuleWithConsent({
    required String id,
    required String pluginName,
    required String nativeModule,
    required String artifactPath,
    required String entrypoint,
    required List<String> capabilities,
    required List<String> permissions,
    required String type,
    PermissionConsentHandler? onPermissionConsent,
  }) async {
    // Step 1: Attempt to load the module normally.
    var result = await runtime.loadNativeModule(
      nativeModule: nativeModule,
      artifactPath: artifactPath,
      entrypoint: entrypoint,
      capabilities: capabilities,
      permissions: permissions,
      type: type,
      config: await configStore.get(id),
    );

    final rawInitialOk = result['ok'];
    final initialOk = rawInitialOk == true ||
        rawInitialOk?.toString().toLowerCase() == 'true';

    if (initialOk) {
      return result;
    }

    // Check if the native runtime is waiting for permissions.
    final pending = _asStringList(result['pending']);

    if (pending.isEmpty) {
      return result;
    }

    debugPrint(
      '[PluginService] Permissions pending for $pluginName: $pending',
    );

    // Step 2: Without a Flutter consent callback, keep it pending.
    if (onPermissionConsent == null) {
      throw PluginPermissionPendingException(
        id: id,
        nativeModule: nativeModule,
        pending: pending,
        permissions: _asStringList(result['permissions'])
            .whenEmpty(permissions),
      );
    }

    // Step 3: Ask the user for plugin-level consent.
    final accepted = await onPermissionConsent(
      pluginName: pluginName,
      nativeModule: nativeModule,
      permissions: pending,
    );

        if (!accepted) {
      throw PluginPermissionDeniedException(
        id: id,
        denied: pending,
        permissions: _asStringList(result['permissions'])
            .whenEmpty(permissions),
      );
    }

    // Step 4a: Persist the user's "Allow" decision.
    //
    // This is what writes ~/.config/pai/permissions.json and calls
    // ApplyEnvBridges() on the native side (so PAI_SCREEN_CONSENT etc.
    // are set). On Linux, `requestPermissions` does NOT do this — it
    // only *reads* the persisted decisions. Without this call the next
    // step always sees decision == "prompt" and returns pending again.
    debugPrint(
      '[PluginService] User consent accepted for $pluginName. '
      'Persisting grant for: $pending',
    );

    try {
      await runtime.grantPermissions(
        nativeModule: nativeModule,
        permissions: pending,
      );
    } catch (e) {
      // Non-fatal: on platforms without a grantPermissions handler
      // requestPermissions below will still be attempted.
      debugPrint(
        '[PluginService] grantPermissions failed (non-fatal): $e',
      );
    }

    // Step 4b: Ask the platform to confirm / raise OS prompts.
    //
    // On Linux this is now a pure verification call — after the grant
    // above it returns `ok: true, granted: [...]`.
    // On Android this is what actually triggers the OS permission
    // dialogs, so we still want it.
    final permissionResult = await runtime.requestPermissions(
      nativeModule: nativeModule,
      permissions: pending,
    );

    final rawPermissionOk = permissionResult['ok'];
    final permissionOk = rawPermissionOk == true ||
        rawPermissionOk?.toString().toLowerCase() == 'true';

    if (!permissionOk) {
      final denied = _asStringList(permissionResult['denied']);

      debugPrint(
        '[PluginService] OS permission request failed: '
        '$permissionResult',
      );

      if (denied.isNotEmpty) {
        throw PluginPermissionDeniedException(
          id: id,
          denied: denied,
          permissions: permissions,
        );
      }

      // Do not attempt loading if OS permission request failed.
      return {
        'ok': false,
        "error": permissionResult['error'] ??
            'os_permission_request_failed',
        "pending": pending,
        "permissions": permissions,
      };
    }

    // Step 5: Permissions granted; retry loading the module.
    debugPrint(
      '[PluginService] OS permissions granted. '
      'Retrying native module load: $nativeModule',
    );

    result = await runtime.loadNativeModule(
      nativeModule: nativeModule,
      artifactPath: artifactPath,
      entrypoint: entrypoint,
      capabilities: capabilities,
      permissions: permissions,
      type: type,
      config: await configStore.get(id),
    );

    final rawRetryOk = result['ok'];
    final retryOk = rawRetryOk == true ||
        rawRetryOk?.toString().toLowerCase() == 'true';

    if (retryOk) {
      debugPrint(
        '[PluginService] Successfully loaded $nativeModule after permission grant.',
      );
      return result;
    }

    // If permissions are still pending, surface a pending exception.
    final stillPending = _asStringList(result['pending']);

    if (stillPending.isNotEmpty) {
      throw PluginPermissionPendingException(
        id: id,
        nativeModule: nativeModule,
        pending: stillPending,
        permissions: _asStringList(result['permissions'])
            .whenEmpty(permissions),
      );
    }

    debugPrint(
      '[PluginService] Module load failed after permission request: $result',
    );

    return result;
  }

  // ---------------------------------------------------------------------------
  // Rollback / recovery
  // ---------------------------------------------------------------------------

  /// Removes the failed version and restores the previous installation entry.
  ///
  /// If the previous plugin was enabled, attempts to reload its native module
  /// so the in-memory registry and runtime state remain consistent.
  Future<void> _rollbackInstall(
    String id,
    String version,
    dynamic previousEntry, {
    DevicePlugin? previousPlugin,
  }) async {
    await installer.removeVersion(id, version);

    await installer.restoreInstalledEntry(
      id,
      previousEntry is Map
          ? Map<String, dynamic>.from(previousEntry)
          : null,
    );

    // Restore the previous in-memory plugin if one existed.
    if (previousPlugin != null) {
      registry.add(previousPlugin);

      if (previousPlugin.enabled) {
        final restored = await _restorePreviousRuntime(previousPlugin);

        if (!restored) {
          previousPlugin.enabled = false;
          await installer.setEnabled(id, false);
        }
      }

      return;
    }

    // If the process had no registry entry, reconstruct the previous version
    // from disk when it still exists.
    if (previousEntry is Map && previousEntry['version'] != null) {
      final previousVersion = previousEntry['version'].toString();
      final manifests = await installer.discover();

      for (final manifest in manifests) {
        if (manifest.id != id || manifest.version != previousVersion) {
          continue;
        }

        final restoredPlugin = DevicePlugin(
          info: manifest.info,
          capabilities: manifest.capabilityDefinitions,
          state: PluginState.disabled,
        );

        registry.add(restoredPlugin);

        if (previousEntry['enabled'] == true) {
          final restored = await _restorePreviousRuntime(restoredPlugin);

          if (restored) {
            restoredPlugin.enabled = true;
          } else {
            await installer.setEnabled(id, false);
          }
        }

        break;
      }
    }
  }

  /// Attempts to reload a previously enabled plugin after an upgrade rollback.
  Future<bool> _restorePreviousRuntime(DevicePlugin plugin) async {
    try {
      final info = plugin.info;
      final platform = _platformKey();
      final spec = _resolvePlatformSpec(info, platform);

      if (spec == null) return false;

      final abi = await runtime.getAbi();

      final artifactFile = await installer.resolveNativeArtifact(
        id: info.id,
        version: info.version,
        platform: platform,
        abi: abi,
        platformSpec: spec,
      );

      if (!await artifactFile.exists()) return false;

      final result = await runtime.loadNativeModule(
        nativeModule: info.runtimeSpec.nativeModule,
        artifactPath: artifactFile.path,
        entrypoint: spec.entrypoint,
        capabilities: plugin.capabilities.map((c) => c.id).toList(),
        permissions: info.permissions,
        type: info.type.name,
        config: await configStore.get(plugin.id),
      );

      return result['ok'] == true;
    } catch (e) {
      debugPrint(
        '[PluginService] Could not restore previous plugin '
        '${plugin.id}: $e',
      );
      return false;
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  /// Resolves the platform-specific artifact declaration.
  PlatformSpec? _resolvePlatformSpec(
    PluginInfo info,
    String platform,
  ) {
    for (final spec in info.platformSpecs) {
      if (spec.platform.toLowerCase() == platform.toLowerCase()) {
        return spec;
      }
    }

    return null;
  }

  /// Returns the current operating system's platform key.
  String _platformKey() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isLinux) return 'linux';
    if (Platform.isWindows) return 'windows';
    if (Platform.isMacOS) return 'macos';
    if (Platform.isIOS) return 'ios';

    throw UnsupportedError('Unknown platform');
  }

  /// Attempts to unload a native module without masking the original error.
  Future<void> _unloadSafely(String nativeModule) async {
    try {
      await runtime.unloadNativeModule(nativeModule);
    } catch (e) {
      debugPrint(
        '[PluginService] unload $nativeModule ignored: $e',
      );
    }
  }

  /// Normalizes a native runtime response into a list of strings.
  List<String> _asStringList(dynamic value) {
    if (value == null) return const [];

    if (value is List) {
      return value.map((e) => e.toString()).toList();
    }

    if (value is String) {
      if (value.isEmpty) return const [];

      return value
          .split(',')
          .map((s) => s.trim())
          .where((s) => s.isNotEmpty)
          .toList();
    }

    return const [];
  }
}

/// Returns a fallback list when the current list is empty.
extension<T> on List<T> {
  List<T> whenEmpty(List<T> fallback) {
    return isEmpty ? fallback : this;
  }
}
