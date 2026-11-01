import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:pai_mobile/plugins/models/plugin_capability.dart';

import '../../plugins/models/plugin_info.dart';
import '../../plugins/plugin_installer.dart';
import '../../plugins/plugin_manager.dart';
import '../../plugins/runtime/plugin_runtime.dart';

/// A developer-facing screen to invoke every installed plugin capability
/// and see its result. Intended for testing camera, clipboard, etc.
class PluginTestScreen extends StatefulWidget {
  final PluginManager manager;
  final PluginInstaller installer;
  final PluginRuntime runtime;

  const PluginTestScreen({
    super.key,
    required this.manager,
    required this.installer,
    required this.runtime,
  });

  @override
  State<PluginTestScreen> createState() => _PluginTestScreenState();
}

class _PluginTestScreenState extends State<PluginTestScreen> {
  @override
  Widget build(BuildContext context) {
    final plugins =
        widget.manager.plugins.where((p) => p.isInstalled).toList();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Plugin Tester'),
        actions: [
          IconButton(
            tooltip: 'Reload plugin list',
            icon: const Icon(Icons.refresh),
            onPressed: () => setState(() {}),
          ),
        ],
      ),
      body: plugins.isEmpty
          ? const Center(
              child: Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                  'No installed plugins.\nInstall one from the Plugins screen first.',
                  textAlign: TextAlign.center,
                ),
              ),
            )
          : ListView(
              padding: const EdgeInsets.all(12),
              children: plugins.map(_buildPluginCard).toList(),
            ),
    );
  }

  Widget _buildPluginCard(PluginInfo plugin) {
    final enabled = plugin.isEnabled;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ExpansionTile(
        leading: Icon(
          enabled ? Icons.check_circle : Icons.pause_circle,
          color: enabled ? Colors.green : Colors.orange,
        ),
        title: Text(plugin.name),
        subtitle: Text(
          '${plugin.version} · ${enabled ? "enabled" : "disabled"} · '
          '${plugin.capabilities.length} capabilities',
        ),
        children: [
          if (plugin.capabilityDetails.isEmpty)
            const ListTile(title: Text('No capabilities in manifest'))
          else
            ...plugin.capabilityDetails.map(
              (cap) => _buildCapabilityTile(plugin, cap),
            ),
        ],
      ),
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
        plugin: plugin,
        capability: cap,
        installer: widget.installer,
        runtime: widget.runtime,
      ),
    );
  }
}

// ==========================================================================
//  Bottom-sheet: run a single capability
// ==========================================================================

class _CapabilityTestSheet extends StatefulWidget {
  final PluginInfo plugin;
  final PluginCapability capability;
  final PluginInstaller installer;
  final PluginRuntime runtime;

  const _CapabilityTestSheet({
    required this.plugin,
    required this.capability,
    required this.installer,
    required this.runtime,
  });

  @override
  State<_CapabilityTestSheet> createState() => _CapabilityTestSheetState();
}

class _CapabilityTestSheetState extends State<_CapabilityTestSheet> {
  // Params from the schema
  final _paramValues = <String, dynamic>{};
  final _controllers = <String, TextEditingController>{};

  // Manifest info resolved on init
  String? _nativeModule;
  String? _artifactPath;
  String? _entrypoint;
  List<String> _allCapabilityIds = [];
  List<String> _allPermissions = [];

  bool _loading = true;
  bool _moduleLoaded = false;
  bool _busy = false;
  bool _requestingPerms = false;
  String? _error;
  Map<String, dynamic>? _result;
  Map<String, dynamic>? _permResult;
  bool _loadedByUs = false;

  @override
  void initState() {
    super.initState();
    _init();
  }

  @override
void dispose() {
  for (final c in _controllers.values) {
    c.dispose();
  }
  if (_loadedByUs && _nativeModule != null) {
    // fire‑and‑forget: do not await in dispose()
    widget.runtime.unloadNativeModule(_nativeModule!);
    debugPrint('[Tester] unloaded $_nativeModule on close');
  }
  super.dispose();
}

  // ---------------------------------------------------------------
  // Init: read manifest, fill defaults, check loaded state
  // ---------------------------------------------------------------
  Future<void> _init() async {
    try {
      // 1. Fill parameter defaults
      for (final entry in widget.capability.properties.entries) {
        final schema = Map<String, dynamic>.from(entry.value as Map);
        dynamic def = schema['default'];

        _paramValues[entry.key] = def;
      }

      // 2. Read manifest from disk
      final dir = await widget.installer.pluginDir(
        widget.plugin.id,
        widget.plugin.version,
      );
      final manifestFile = File('${dir.path}/manifest.json');
      if (!await manifestFile.exists()) {
        throw Exception('manifest.json missing at ${manifestFile.path}');
      }
      final manifest =
          jsonDecode(await manifestFile.readAsString()) as Map<String, dynamic>;

      // 3. Extract native module / entrypoint / artifact
      final runtime = (manifest['runtime'] as Map?)?.cast<String, dynamic>();

      _nativeModule = runtime?['nativeModule']?.toString()
          ?? _pickString(
              manifest,
              const ['nativeModule', 'native_module', 'module_name'],
              fallback: widget.plugin.id,
            );

      _entrypoint = runtime?['entrypoint']?.toString()
          ?? _pickString(
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

      // 4. Gather capability ids + permissions for load
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

      // 5. Is it already loaded?
      _moduleLoaded = await widget.runtime.isModuleAvailable(_nativeModule!);
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

  // ---------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------
  Future<void> _loadModule() async {
  setState(() { _busy = true; _error = null; });
  try {
    // Was it already loaded by PluginService at startup?
    final wasLoaded =
        await widget.runtime.isModuleAvailable(_nativeModule!);

    final ok = await widget.runtime.loadNativeModule(
      nativeModule: _nativeModule!,
      artifactPath: _artifactPath!,
      entrypoint: _entrypoint!,
      capabilities: _allCapabilityIds,
      permissions: _allPermissions,
    );

    if (ok && !wasLoaded) {
      _loadedByUs = true;                 // only unload what we loaded
    }

    _moduleLoaded = ok;

    if (ok) {
      final stillThere =
          await widget.runtime.isModuleAvailable(_nativeModule!);
      debugPrint('[Tester] post-load isModuleAvailable($_nativeModule) '
          '→ $stillThere');
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
    setState(() {
      _requestingPerms = true;
      _permResult = null;
      _error = null;
    });
    try {
      _permResult = await widget.runtime.requestPermissions(
        nativeModule: _nativeModule!,
        permissions: widget.capability.permissions,
      );
    } catch (e) {
      _error = e.toString();
    } finally {
      if (mounted) setState(() => _requestingPerms = false);
    }
  }

  Future<void> _invoke() async {
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
      final r = await widget.runtime.invoke(
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

  // ---------------------------------------------------------------
  // Build
  // ---------------------------------------------------------------
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
                        const Text('Parameters',
                            style: TextStyle(fontWeight: FontWeight.w700)),
                        const SizedBox(height: 8),
                        ..._paramValues.keys.map(_buildParamField),
                        const SizedBox(height: 8),
                        if (_error != null) _errorBox(_error!),
                        if (_permResult != null) ...[
                          const SizedBox(height: 12),
                          const Text('Permissions result',
                              style: TextStyle(fontWeight: FontWeight.w700)),
                          const SizedBox(height: 4),
                          _jsonBox(_permResult!),
                        ],
                        if (_result != null) ...[
                          const SizedBox(height: 12),
                          const Text('Result',
                              style: TextStyle(fontWeight: FontWeight.w700)),
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

  Widget _header() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(widget.capability.id,
                    style: const TextStyle(
                        fontSize: 18, fontWeight: FontWeight.w700)),
                if (widget.capability.description.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(widget.capability.description,
                        style: TextStyle(color: Colors.grey.shade700)),
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

  Widget _metaSection() {
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
          Text('nativeModule: $_nativeModule',
              style: const TextStyle(fontSize: 12, fontFamily: 'monospace')),
        if (_artifactPath != null)
          Text('artifact: $_artifactPath',
              style: const TextStyle(fontSize: 12, fontFamily: 'monospace')),
        if (widget.capability.permissions.isNotEmpty) ...[
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: widget.capability.permissions
                .map((p) => Chip(
                      label: Text(p, style: const TextStyle(fontSize: 11)),
                      visualDensity: VisualDensity.compact,
                      materialTapTargetSize:
                          MaterialTapTargetSize.shrinkWrap,
                    ))
                .toList(),
          ),
        ],
      ],
    );
  }

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

  Widget _footer() {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
        child: Column(
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
                          child: CircularProgressIndicator(strokeWidth: 2))
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
                    label: Text(_moduleLoaded ? 'Reload module' : 'Load module'),
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
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.play_arrow, size: 18),
                    label: const Text('Invoke'),
                    onPressed: _loading || _busy ? null : _invoke,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  // ---------------------------------------------------------------
  // Parameter field rendering (schema-driven)
  // ---------------------------------------------------------------
  Widget _buildParamField(String key) {
    final rawSchema = widget.capability.properties[key];
    if (rawSchema is! Map) return const SizedBox.shrink();
    final schema = Map<String, dynamic>.from(rawSchema);
    final type = schema['type'] as String? ?? 'string';
    final enums = schema['enum'] as List?;
    final desc = schema['description'] as String? ?? '';
    final value = _paramValues[key];

    Widget input;
    if (enums != null && enums.isNotEmpty) {
      input = DropdownButtonFormField<dynamic>(
        value: value ?? enums.first,
        items: enums
            .map((v) => DropdownMenuItem(value: v, child: Text(v.toString())))
            .toList(),
        onChanged: (v) => setState(() => _paramValues[key] = v),
        decoration:
            const InputDecoration(border: OutlineInputBorder(), isDense: true),
      );
    } else if (type == 'boolean') {
      input = SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: Text(value == true ? 'true' : 'false',
            style: const TextStyle(fontSize: 13)),
        value: value == true,
        onChanged: (v) => setState(() => _paramValues[key] = v),
      );
    } else {
      final ctrl = _controllers.putIfAbsent(
        key,
        () => TextEditingController(text: value?.toString() ?? ''),
      );
      final isNum = type == 'integer' || type == 'number';
      input = TextFormField(
        controller: ctrl,
        keyboardType: isNum
            ? TextInputType.numberWithOptions(decimal: type == 'number')
            : TextInputType.text,
        onChanged: (v) {
          if (type == 'integer') {
            _paramValues[key] = int.tryParse(v);
          } else if (type == 'number') {
            _paramValues[key] = double.tryParse(v);
          } else {
            _paramValues[key] = v;
          }
        },
        decoration:
            const InputDecoration(border: OutlineInputBorder(), isDense: true),
      );
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(key, style: const TextStyle(fontWeight: FontWeight.w500)),
          if (desc.isNotEmpty)
            Text(desc,
                style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
          const SizedBox(height: 4),
          input,
        ],
      ),
    );
  }
}