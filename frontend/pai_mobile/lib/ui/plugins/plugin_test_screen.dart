import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:pai_mobile/plugins/models/catalog_response.dart';
import 'package:pai_mobile/plugins/models/plugin_capability.dart';

import '../../plugins/models/plugin_info.dart';
import '../../plugins/plugin_installer.dart';
import '../../plugins/plugin_manager.dart';
import '../../plugins/runtime/plugin_runtime.dart';
import 'widgets/param_field.dart';


/// Unified plugin tester.
///
/// Two modes:
///
///   • **Local** — `device == null`. Runs capabilities through the
///     MethodChannel runtime (`runtime.invoke`), shows module-load and
///     permission buttons, reads `manifest.json` from disk.
///
///   • **Remote** — `device != null`. Runs capabilities through the
///     backend's `POST /devices/{id}/invoke` endpoint. No local module
///     state to manage; enablement is derived from `plugin.isEnabled`.
///
/// Three call shapes:
///
///   PluginTestScreen(manager, installer, runtime)                 → local, all installed plugins
///   PluginTestScreen(manager, installer, runtime, plugin: p)      → local, one plugin
///   PluginTestScreen(manager, plugin: p, device: d)               → remote, one plugin on `d`
class PluginTestScreen extends StatefulWidget {
  final PluginManager manager;

  /// Required for local mode. Ignored for remote mode.
  final PluginInstaller? installer;
  final PluginRuntime? runtime;

  /// If set, the screen shows only this plugin's capabilities instead
  /// of the full list of installed local plugins.
  final PluginInfo? plugin;

  /// If set, capabilities are invoked via the backend HTTP route.
  /// If null, invocation goes through the local MethodChannel runtime.
  final DeviceDescriptor? device;

  const PluginTestScreen({
    super.key,
    required this.manager,
    this.installer,
    this.runtime,
    this.plugin,
    this.device,
  });

  @override
  State<PluginTestScreen> createState() => _PluginTestScreenState();
}

class _PluginTestScreenState extends State<PluginTestScreen> {
  bool get _isRemote => widget.device != null;
  bool get _isFocused => widget.plugin != null;

  @override
  Widget build(BuildContext context) {
    if (_isFocused) return _buildFocused();
    return _buildGlobal();
  }

  // ------------------------------------------------------------------
  // Focused (single plugin)
  // ------------------------------------------------------------------

  Widget _buildFocused() {
    final plugin = widget.plugin!;
    final device = widget.device;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          _isRemote
              ? '${plugin.name} on ${device!.label}'
              : plugin.name,
        ),
        actions: [
          IconButton(
            tooltip: 'Reload from catalog',
            icon: const Icon(Icons.refresh),
            onPressed: () async {
              await widget.manager.loadCatalog();
              if (!mounted) return;
              setState(() {});
            },
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: widget.manager.loadCatalog,
        child: ListView(
          padding: const EdgeInsets.all(12),
          children: [
            if (_isRemote && !device!.connected)
              _offlineBanner(device),
            _buildPluginCard(plugin, forceExpanded: true),
          ],
        ),
      ),
    );
  }

  Widget _offlineBanner(DeviceDescriptor device) {
    return Card(
      color: Colors.orange.shade50,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: [
            Icon(Icons.cloud_off, color: Colors.orange.shade700),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                '${device.label} is offline. '
                'Invokes will fail until the device reconnects.',
                style: TextStyle(
                  fontSize: 12,
                  color: Colors.orange.shade900,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // ------------------------------------------------------------------
  // Global (all installed local plugins)
  // ------------------------------------------------------------------

  Widget _buildGlobal() {
    final plugins =
        widget.manager.plugins.where((p) => p.isInstalled).toList();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Plugin Tester'),
        actions: [
          IconButton(
            tooltip: 'Reload plugin list',
            icon: const Icon(Icons.refresh),
            onPressed: () async {
              await widget.manager.loadCatalog();
              if (!mounted) return;
              setState(() {});
            },
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: widget.manager.loadCatalog,
        child: plugins.isEmpty
            ? ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: const [
                  SizedBox(height: 200),
                  Center(
                    child: Padding(
                      padding: EdgeInsets.all(24),
                      child: Text(
                        'No installed plugins.\n'
                        'Install one from the Plugins screen first.',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  ),
                ],
              )
            : ListView(
                padding: const EdgeInsets.all(12),
                children: plugins
                    .map((p) => _buildPluginCard(p, forceExpanded: false))
                    .toList(),
              ),
      ),
    );
  }

  // ------------------------------------------------------------------
  // Shared card
  // ------------------------------------------------------------------

  Widget _buildPluginCard(
    PluginInfo plugin, {
    required bool forceExpanded,
  }) {
    final enabled = plugin.isEnabled;
    final installed = plugin.isInstalled;

    final header = ExpansionTile(
      initiallyExpanded: forceExpanded,
      leading: Icon(
        !installed
            ? Icons.error_outline
            : enabled
                ? Icons.check_circle
                : Icons.pause_circle,
        color: !installed
            ? Colors.red
            : enabled
                ? Colors.green
                : Colors.orange,
      ),
      title: Text(plugin.name),
      subtitle: Text(
        '${plugin.version} · '
        '${!installed ? "not installed" : enabled ? "enabled" : "disabled"} · '
        '${plugin.capabilities.length} capabilities',
      ),
      children: [
        if (!installed)
          const ListTile(
            leading: Icon(Icons.info_outline),
            title: Text(
              'Plugin is not installed on this device. '
              'Install it from the Plugins screen first.',
              style: TextStyle(fontSize: 13),
            ),
          )
        else if (!enabled)
          ListTile(
            leading: const Icon(Icons.warning_amber),
            title: const Text(
              'Plugin is installed but disabled. '
              'Enable it from the Plugins screen to invoke capabilities.',
              style: TextStyle(fontSize: 13),
            ),
          )
        else if (plugin.capabilityDetails.isEmpty)
          const ListTile(title: Text('No capabilities in manifest'))
        else
          ...plugin.capabilityDetails.map(
            (cap) => _buildCapabilityTile(plugin, cap),
          ),
      ],
    );

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: header,
    );
  }

  Widget _buildCapabilityTile(PluginInfo plugin, PluginCapability cap) {
    return ListTile(
      leading: const Icon(Icons.build_outlined),
      title: Text(cap.id),
      subtitle: cap.description.isEmpty ? null : Text(cap.description),
      trailing: ElevatedButton.icon(
        icon: const Icon(Icons.play_arrow, size: 18),
        label: const Text('Test'),
        onPressed: () => _openTestSheet(plugin, cap),
      ),
    );
  }

  Future<void> _openTestSheet(PluginInfo plugin, PluginCapability cap) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _CapabilityTestSheet(
        manager: widget.manager,
        plugin: plugin,
        capability: cap,
        installer: widget.installer,
        runtime: widget.runtime,
        device: widget.device,
      ),
    );
  }
}


// ==========================================================================
// Bottom sheet — unified local + remote
// ==========================================================================

class _CapabilityTestSheet extends StatefulWidget {
  final PluginManager manager;
  final PluginInfo plugin;
  final PluginCapability capability;
  final PluginInstaller? installer;
  final PluginRuntime? runtime;
  final DeviceDescriptor? device;

  const _CapabilityTestSheet({
    required this.manager,
    required this.plugin,
    required this.capability,
    this.installer,
    this.runtime,
    this.device,
  });

  @override
  State<_CapabilityTestSheet> createState() => _CapabilityTestSheetState();
}

class _CapabilityTestSheetState extends State<_CapabilityTestSheet> {
  bool get _isRemote => widget.device != null;

  // ----- shared -----
  final _paramValues = <String, dynamic>{};
  bool _busy = false;
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _result;
  Map<String, dynamic>? _permResult;
  bool _requestingPerms = false;

  // ----- local-only -----
  String? _nativeModule;
  String? _artifactPath;
  String? _entrypoint;
  List<String> _allCapabilityIds = [];
  List<String> _allPermissions = [];
  bool _moduleLoaded = false;
  bool _loadedByUs = false;

  @override
  void initState() {
    super.initState();
    _seedParamDefaults();
    if (_isRemote) {
      _loading = false;
    } else {
      _initLocal();
    }
  }

  @override
  void dispose() {
    if (!_isRemote && _loadedByUs && _nativeModule != null) {
      // fire-and-forget — do not await in dispose()
      widget.runtime?.unloadNativeModule(_nativeModule!);
      debugPrint('[Tester] unloaded $_nativeModule on close');
    }
    super.dispose();
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  void _seedParamDefaults() {
    // Seed defaults from the property schema. For object/array params
    // without an explicit default, seed `{}` / `[]` so the editors
    // open in a valid empty state instead of `null`.
    for (final entry in widget.capability.properties.entries) {
      final rawSchema = entry.value;
      if (rawSchema is! Map) {
        _paramValues[entry.key] = null;
        continue;
      }
      final schema = Map<String, dynamic>.from(rawSchema);
      final type = schema['type'] as String? ?? 'string';
      dynamic def = schema['default'];
      if (def == null) {
        if (type == 'object') {
          def = <String, dynamic>{};
        } else if (type == 'array') {
          def = <dynamic>[];
        }
      }
      _paramValues[entry.key] = def;
    }
  }

  Future<void> _initLocal() async {
    try {
      // Local mode requires installer + runtime.
      if (widget.installer == null || widget.runtime == null) {
        throw StateError(
          'Local testing requires installer + runtime; '
          'pass them via the constructor or use remote mode.',
        );
      }

      final dir = await widget.installer!.pluginDir(
        widget.plugin.id,
        widget.plugin.version,
      );
      final manifestFile = File('${dir.path}/manifest.json');
      if (!await manifestFile.exists()) {
        throw Exception('manifest.json missing at ${manifestFile.path}');
      }
      final manifest =
          jsonDecode(await manifestFile.readAsString()) as Map<String, dynamic>;

      final runtime = (manifest['runtime'] as Map?)?.cast<String, dynamic>();

      _nativeModule = runtime?['nativeModule']?.toString() ??
          _pickString(
            manifest,
            const ['nativeModule', 'native_module', 'module_name'],
            fallback: widget.plugin.id,
          );

      _entrypoint = runtime?['entrypoint']?.toString() ??
          _pickString(
            manifest,
            const ['entrypoint', 'entry_point', 'init'],
            fallback: 'pai_plugin_module_create',
          );

      final platform = _platformKey();
      final platforms = manifest['platforms'] as Map?;
      final spec = platforms?[platform] as Map?;
      if (spec == null) {
        throw Exception('Manifest has no platforms.$platform entry');
      }
      final artifactName = _pickString(
        spec,
        const ['artifact', 'file', 'binary'],
        fallback: '',
      );
      if (artifactName.isEmpty) {
        throw Exception('Manifest spec has no artifact filename');
      }
      _artifactPath = '${dir.path}/$artifactName';

      final capsRaw = manifest['capabilities'] as List? ?? const [];
      _allCapabilityIds = capsRaw
          .map((c) {
            if (c is Map) return c['id']?.toString();
            return c?.toString();
          })
          .whereType<String>()
          .toList();

      final permsRaw = manifest['permissions'] as List? ?? const [];
      _allPermissions = permsRaw.map((p) => p.toString()).toList();

      _moduleLoaded = await widget.runtime!.isModuleAvailable(_nativeModule!);
    } catch (e) {
      _error = 'Init failed: $e';
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _pickString(
    Map<dynamic, dynamic> map,
    List<String> keys, {
    required String fallback,
  }) {
    for (final k in keys) {
      final v = map[k];
      if (v != null && v.toString().isNotEmpty) return v.toString();
    }
    return fallback;
  }

  String _platformKey() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isLinux) return 'linux';
    if (Platform.isWindows) return 'windows';
    if (Platform.isMacOS) return 'macos';
    if (Platform.isIOS) return 'ios';
    throw Exception('unknown platform');
  }

  // ------------------------------------------------------------------
  // Actions — local
  // ------------------------------------------------------------------

  Future<void> _loadModule() async {
    if (widget.runtime == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final wasLoaded =
          await widget.runtime!.isModuleAvailable(_nativeModule!);

      final ok = await widget.runtime!.loadNativeModule(
        nativeModule: _nativeModule!,
        artifactPath: _artifactPath!,
        entrypoint: _entrypoint!,
        capabilities: _allCapabilityIds,
        permissions: _allPermissions,
      );

      if (ok && !wasLoaded) _loadedByUs = true;
      _moduleLoaded = ok;

      if (ok) {
        final stillThere =
            await widget.runtime!.isModuleAvailable(_nativeModule!);
        if (!stillThere) {
          _error = 'Host reported load success, but isModuleAvailable '
              'returns false — the native registry is not persistent.';
        }
      } else {
        _error = 'Native module load returned false';
      }
    } catch (e) {
      _error = e.toString();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _requestPermissions() async {
    if (widget.runtime == null) return;
    setState(() {
      _requestingPerms = true;
      _permResult = null;
      _error = null;
    });
    try {
      _permResult = await widget.runtime!.requestPermissions(
        nativeModule: _nativeModule!,
        permissions: widget.capability.permissions,
      );
    } catch (e) {
      _error = e.toString();
    } finally {
      if (mounted) setState(() => _requestingPerms = false);
    }
  }

  Future<void> _invokeLocal() async {
    if (widget.runtime == null) return;
    if (!_moduleLoaded) {
      await _loadModule();
      if (!_moduleLoaded) return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final r = await widget.runtime!.invoke(
        nativeModule: _nativeModule!,
        capability: widget.capability.id,
        parameters: _paramValues,
      );
      _result = r;
    } catch (e) {
      _error = e.toString();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ------------------------------------------------------------------
  // Actions — remote
  // ------------------------------------------------------------------

  Future<void> _invokeRemote() async {
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final res = await widget.manager.api.post(
        '/devices/${widget.device!.deviceId}/invoke',
        {
          'capability': widget.capability.id,
          'parameters': _paramValues,
          'timeout_seconds': 60,
        },
      );
      final raw = res['result'];
      _result = raw is Map
          ? Map<String, dynamic>.from(raw)
          : {'result': raw};
    } catch (e) {
      _error = e.toString();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _invoke() => _isRemote ? _invokeRemote() : _invokeLocal();

  // ------------------------------------------------------------------
  // Build
  // ------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final insets = MediaQuery.of(context).viewInsets.bottom;
    final maxHeight = MediaQuery.of(context).size.height * 0.9;

    return Padding(
      padding: EdgeInsets.only(bottom: insets),
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: maxHeight),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            _header(),
            const Divider(height: 1),
            Expanded(
              child: _loading
                  ? const Center(child: CircularProgressIndicator())
                  : ListView(
                      padding: const EdgeInsets.all(16),
                      children: [
                        _metaSection(),
                        const SizedBox(height: 16),
                        const Text(
                          'Parameters',
                          style: TextStyle(fontWeight: FontWeight.w700),
                        ),
                        const SizedBox(height: 8),
                        if (_paramValues.isEmpty)
                          const Text(
                            'No parameters.',
                            style: TextStyle(
                              fontSize: 12,
                              color: Colors.grey,
                            ),
                          )
                        else
                          ..._paramValues.keys.map(_buildParamField),
                        const SizedBox(height: 8),
                        if (_error != null) _errorBox(_error!),
                        if (_permResult != null) ...[
                          const SizedBox(height: 12),
                          const Text(
                            'Permissions result',
                            style: TextStyle(fontWeight: FontWeight.w700),
                          ),
                          const SizedBox(height: 4),
                          _jsonBox(_permResult!),
                        ],
                        if (_result != null) ...[
                          const SizedBox(height: 12),
                          const Text(
                            'Result',
                            style: TextStyle(fontWeight: FontWeight.w700),
                          ),
                          const SizedBox(height: 4),
                          _jsonBox(_result!),
                        ],
                      ],
                    ),
            ),
            _footer(),
          ],
        ),
      ),
    );
  }

  // ------------------------------------------------------------------
  // Param field — uses the shared ParamField widget
  // ------------------------------------------------------------------

  Widget _buildParamField(String key) {
    final rawSchema = widget.capability.properties[key];
    if (rawSchema is! Map) return const SizedBox.shrink();
    return ParamField(
      name: key,
      schema: Map<String, dynamic>.from(rawSchema),
      value: _paramValues[key],
      onChanged: (v) => setState(() => _paramValues[key] = v),
    );
  }

  // ------------------------------------------------------------------
  // Header
  // ------------------------------------------------------------------

  Widget _header() {
    final titleSuffix = _isRemote
        ? ' on ${widget.device!.label}'
        : '';
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${widget.capability.id}$titleSuffix',
                  style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                if (widget.capability.description.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      widget.capability.description,
                      style: TextStyle(color: Colors.grey.shade700),
                    ),
                  ),
              ],
            ),
          ),
          IconButton(
            icon: const Icon(Icons.close),
            onPressed: () => Navigator.of(context).pop(),
          ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------------
  // Meta section — branches local vs remote
  // ------------------------------------------------------------------

  Widget _metaSection() {
    if (_isRemote) {
      final enabled = widget.plugin.isEnabled;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                enabled ? Icons.check_circle : Icons.info_outline,
                size: 16,
                color: enabled ? Colors.green : Colors.orange,
              ),
              const SizedBox(width: 4),
              Text(
                enabled
                    ? 'Plugin enabled on ${widget.device!.label}'
                    : 'Plugin disabled on ${widget.device!.label}',
                style: TextStyle(
                  color: enabled ? Colors.green : Colors.orange,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'device: ${widget.device!.deviceId}',
            style: const TextStyle(
              fontSize: 12,
              fontFamily: 'monospace',
            ),
          ),
          Text(
            'platform: ${widget.device!.platform}/${widget.device!.architecture}',
            style: const TextStyle(
              fontSize: 12,
              fontFamily: 'monospace',
            ),
          ),
          if (widget.capability.permissions.isNotEmpty) ...[
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: widget.capability.permissions
                  .map(
                    (p) => Chip(
                      label: Text(
                        p,
                        style: const TextStyle(fontSize: 11),
                      ),
                      visualDensity: VisualDensity.compact,
                      materialTapTargetSize:
                          MaterialTapTargetSize.shrinkWrap,
                    ),
                  )
                  .toList(),
            ),
          ],
        ],
      );
    }

    // Local mode
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(
              _moduleLoaded ? Icons.check_circle : Icons.info_outline,
              size: 16,
              color: _moduleLoaded ? Colors.green : Colors.orange,
            ),
            const SizedBox(width: 4),
            Text(
              _moduleLoaded ? 'Module loaded' : 'Module not loaded',
              style: TextStyle(
                color: _moduleLoaded ? Colors.green : Colors.orange,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        if (_nativeModule != null)
          Text(
            'nativeModule: $_nativeModule',
            style: const TextStyle(
              fontSize: 12,
              fontFamily: 'monospace',
            ),
          ),
        if (_artifactPath != null)
          Text(
            'artifact: $_artifactPath',
            style: const TextStyle(
              fontSize: 12,
              fontFamily: 'monospace',
            ),
          ),
        if (widget.capability.permissions.isNotEmpty) ...[
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: widget.capability.permissions
                .map(
                  (p) => Chip(
                    label: Text(
                      p,
                      style: const TextStyle(fontSize: 11),
                    ),
                    visualDensity: VisualDensity.compact,
                    materialTapTargetSize:
                        MaterialTapTargetSize.shrinkWrap,
                  ),
                )
                .toList(),
          ),
        ],
      ],
    );
  }

  // ------------------------------------------------------------------
  // Error + JSON boxes
  // ------------------------------------------------------------------

  Widget _errorBox(String msg) {
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 6),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Colors.red.shade50,
        border: Border.all(color: Colors.red.shade200),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(msg, style: const TextStyle(color: Colors.red)),
    );
  }

  Widget _jsonBox(Map<String, dynamic> data) {
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Colors.grey.shade100,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.grey.shade300),
      ),
      child: SelectableText(
        const JsonEncoder.withIndent('  ').convert(data),
        style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
      ),
    );
  }

  // ------------------------------------------------------------------
  // Footer — branches local vs remote
  // ------------------------------------------------------------------

  Widget _footer() {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
        child: _isRemote ? _remoteFooter() : _localFooter(),
      ),
    );
  }

  Widget _remoteFooter() {
    return Row(
      children: [
        Expanded(
          child: OutlinedButton(
            onPressed: _busy ? null : () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: ElevatedButton.icon(
            icon: _busy
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.play_arrow, size: 18),
            label: const Text('Invoke'),
            onPressed: _busy ? null : _invoke,
          ),
        ),
      ],
    );
  }

  Widget _localFooter() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (widget.capability.permissions.isNotEmpty)
          SizedBox(
            width: double.infinity,
            child: OutlinedButton.icon(
              icon: _requestingPerms
                  ? const SizedBox(
                      width: 14,
                      height: 14,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.lock_open, size: 18),
              label: const Text('Request permissions'),
              onPressed: _loading || _busy || _requestingPerms
                  ? null
                  : _requestPermissions,
            ),
          ),
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                icon: const Icon(Icons.download, size: 18),
                label: Text(
                  _moduleLoaded ? 'Reload module' : 'Load module',
                ),
                onPressed: _loading || _busy ? null : _loadModule,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: ElevatedButton.icon(
                icon: _busy
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.play_arrow, size: 18),
                label: const Text('Invoke'),
                onPressed: _loading || _busy ? null : _invoke,
              ),
            ),
          ],
        ),
      ],
    );
  }
}