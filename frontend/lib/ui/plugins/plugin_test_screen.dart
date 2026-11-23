
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:pai_mobile/models/device.dart';
import 'package:pai_mobile/plugins/models/plugin_capability.dart';

import '../../plugins/models/plugin_info.dart';
import '../../plugins/plugin_manager.dart';
import '../../plugins/capability_dispatcher.dart';
import 'widgets/param_field.dart';

/// Unified plugin tester.
///
/// Local:
///   PluginTestScreen(
///     manager: manager,
///     dispatcher: dispatcher,
///     thisDeviceId: deviceId,
///     plugin: plugin,
///   )
///
/// Remote:
///   PluginTestScreen(
///     manager: manager,
///     dispatcher: dispatcher,
///     thisDeviceId: deviceId,
///     plugin: plugin,
///     device: remoteDevice,
///     remoteCapabilities: capabilitiesFromBackend,
///   )
///
/// Global local tester:
///   PluginTestScreen(
///     manager: manager,
///     dispatcher: dispatcher,
///     thisDeviceId: deviceId,
///   )
///
/// Local invocation always follows:
///
/// PluginTestScreen
///      -> CapabilityDispatcher
///      -> PluginRegistry
///      -> PluginInstaller
///      -> PluginRuntime
class PluginTestScreen extends StatefulWidget {
  final PluginManager manager;
  final CapabilityDispatcher dispatcher;
  final String thisDeviceId;

  /// When supplied, only this plugin is displayed.
  final PluginInfo? plugin;

  /// Null or the current device means local mode.
  /// A different device means remote mode.
  final DeviceInfo device;

  /// Full capability definitions for remote plugins.
  ///
  /// PluginInfo.capabilities contains capability IDs only.
  /// Remote capability schemas must come from the backend/device manifest.
  final List<PluginCapability> remoteCapabilities;

  const PluginTestScreen({
    super.key,
    required this.manager,
    required this.dispatcher,
    required this.thisDeviceId,
    this.plugin,
    required this.device,
    this.remoteCapabilities = const [],
  });

  @override
  State<PluginTestScreen> createState() => _PluginTestScreenState();
}

class _PluginTestScreenState extends State<PluginTestScreen> {
  @override
  void initState() {
    super.initState();
    debugPrint('PluginTestScreenState initialized with plugin: ${widget.plugin?.id}, device: ${widget.device.id} thisDeviceId: ${widget.thisDeviceId}');
  }

  bool get _isRemote => widget.device.id != widget.thisDeviceId;

  bool get _isFocused => widget.plugin != null;

  @override
  Widget build(BuildContext context) {
    return _isFocused ? _buildFocused() : _buildGlobal();
  }

  // ------------------------------------------------------------------
  // Focused plugin
  // ------------------------------------------------------------------

  Widget _buildFocused() {
    final plugin = widget.plugin!;
    final device = widget.device;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          _isRemote ? '${plugin.name} on ${device.name}'
              : plugin.name,
        ),
        actions: [
          IconButton(
            tooltip: 'Reload from catalog',
            icon: const Icon(Icons.refresh),
            onPressed: _reload,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: widget.manager.loadCatalog,
        child: ListView(
          padding: const EdgeInsets.all(12),
          children: [
            if (_isRemote  && !device.connected)
              _offlineBanner(device),
            _buildPluginCard(plugin, forceExpanded: true),
          ],
        ),
      ),
    );
  }

  Future<void> _reload() async {
    await widget.manager.loadCatalog();

    if (!mounted) return;

    setState(() {});
  }

  Widget _offlineBanner(DeviceInfo device) {
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
                '${device.name} is offline. '
                'Invocations will fail until the device reconnects.',
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
  // Global local tester
  // ------------------------------------------------------------------

  Widget _buildGlobal() {
    final plugins = widget.manager.thisDevicePlugins
        .where((plugin) => widget.manager.isInstalled(plugin.id))
        .toList();

    return Scaffold(
      appBar: AppBar(              
        title: const Text('Plugin Tester'),
        actions: [
          IconButton(
            tooltip: 'Reload plugin list',
            icon: const Icon(Icons.refresh),
            onPressed: _reload,
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
                    .map(
                      (plugin) => _buildPluginCard(
                        plugin,
                        forceExpanded: false,
                      ),
                    )
                    .toList(),
              ),
      ),
    );
  }

  // ------------------------------------------------------------------
  // Capability definitions
  // ------------------------------------------------------------------

  List<PluginCapability> _capabilitiesFor(PluginInfo plugin) {
  if (_isRemote) {
    debugPrint(
      '[PluginTester] Remote capabilities: '
      '${widget.remoteCapabilities.map((c) => c.id).toList()}',
    );

    return widget.remoteCapabilities;
  }

  final registered = widget.manager.service.registry.byId(plugin.id);

  debugPrint('[PluginTester] Plugin ID: ${plugin.id}');
  debugPrint('[PluginTester] Registry found: ${registered != null}');
  debugPrint('[PluginTester] Registry contents: '                                                                                                          
      '${registered?.capabilities.map((c) => c.id).toList()}');

  return registered?.capabilities ?? const [];
}

  // ------------------------------------------------------------------
  // Plugin card
  // ------------------------------------------------------------------

  Widget _buildPluginCard(
    PluginInfo plugin, {
    required bool forceExpanded,
  }) {
    final installed = _isRemote
        ? widget.manager.isInstalledOnDevice(
            plugin.id,      
            widget.device.id,
          )
        : widget.manager.isInstalled(plugin.id);

    final enabled = _isRemote
        ? widget.manager.isEnabledOnDevice(
            plugin.id,
            widget.device.id,
          )
        : widget.manager.isEnabled(plugin.id);

    final capabilities = _capabilitiesFor(plugin);

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ExpansionTile(
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
          '${!installed ? "not installed" : enabled ? "enabled" : "disabled"}'
          ' · ${capabilities.length} capabilities',
        ),
        children: [
          if (!installed)
            const ListTile(
              leading: Icon(Icons.info_outline),
              title: Text(
                'Plugin is not installed on this device.',
                style: TextStyle(fontSize: 13),
              ),
            )
          else if (!enabled)
            const ListTile(
              leading: Icon(Icons.warning_amber),
              title: Text(
                'Plugin is disabled. Enable it before invoking capabilities.',
                style: TextStyle(fontSize: 13),
              ),
            )
          else if (capabilities.isEmpty)
            ListTile(
              title: const Text('No capability definitions available'),
              subtitle: Text(
                _isRemote
                    ? 'Load the capability definitions from the remote device manifest.'
                    : 'Check the installed plugin manifest and registry.',
              ),
            )
          else
            ...capabilities.map(
              (capability) => _buildCapabilityTile(plugin, capability),
            ),
        ],
      ),
    );
  }

  Widget _buildCapabilityTile(
    PluginInfo plugin,
    PluginCapability capability,
  ) {
    return ListTile(
      leading: const Icon(Icons.build_outlined),
      title: Text(capability.id),
      subtitle: capability.description.isEmpty
          ? null
          : Text(capability.description),
      trailing: ElevatedButton.icon(
        icon: const Icon(Icons.play_arrow, size: 18),
        label: const Text('Test'),
        onPressed: () => _openTestSheet(plugin, capability),
      ),
    );
  }

  Future<void> _openTestSheet(
    PluginInfo plugin,
    PluginCapability capability,
  ) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _CapabilityTestSheet(
        manager: widget.manager,
        dispatcher: widget.dispatcher,
        plugin: plugin,
        capability: capability,
        device: widget.device,
        thisDeviceId: widget.thisDeviceId,
      ),
    );
  }
}

// ==========================================================================
// Capability test bottom sheet
// ==========================================================================

class _CapabilityTestSheet extends StatefulWidget {
  final PluginManager manager;
  final CapabilityDispatcher dispatcher;
  final PluginInfo plugin;
  final PluginCapability capability;
  final DeviceInfo? device;
  final String thisDeviceId;

  const _CapabilityTestSheet({
    required this.manager,
    required this.dispatcher,
    required this.plugin,
    required this.capability,
    required this.thisDeviceId,
    required this.device,
  });

  @override
  State<_CapabilityTestSheet> createState() =>
      _CapabilityTestSheetState();
}

class _CapabilityTestSheetState extends State<_CapabilityTestSheet> {
  bool get _isRemote =>
      widget.device != null &&
      widget.device!.id != widget.thisDeviceId;

  final Map<String, dynamic> _paramValues = {};

  bool _busy = false;
  bool _showCodepoints = false;

  String? _error;
  Map<String, dynamic>? _result;

  @override
  void initState() {
    super.initState();
    _seedParamDefaults();
  }

  // ------------------------------------------------------------------
  // Parameters
  // ------------------------------------------------------------------

  void _seedParamDefaults() {
    for (final entry in widget.capability.properties.entries) {
      final rawSchema = entry.value;

      if (rawSchema is! Map) {
        _paramValues[entry.key] = null;
        continue;
      }

      final schema = Map<String, dynamic>.from(rawSchema);
      final type = schema['type'] as String? ?? 'string';

      dynamic defaultValue = schema['default'];

      if (defaultValue == null) {
        if (type == 'object') {
          defaultValue = <String, dynamic>{};
        } else if (type == 'array') {
          defaultValue = <dynamic>[];
        }
      }

      _paramValues[entry.key] = defaultValue;
    }
  }

  Widget _buildParamField(String key) {
    final rawSchema = widget.capability.properties[key];

    if (rawSchema is! Map) {
      return const SizedBox.shrink();
    }

    return ParamField(
      name: key,
      schema: Map<String, dynamic>.from(rawSchema),
      value: _paramValues[key],
      onChanged: (value) {
        setState(() {
          _paramValues[key] = value;
        });
      },
    );
  }

  // ------------------------------------------------------------------
  // Invocation
  // ------------------------------------------------------------------

  Future<void> _invoke() async {
    if (_busy) return;

    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });

    try {
      if (_isRemote) {
        await _invokeRemote();
      } else {
        await _invokeLocal();
      }
    } catch (e, stackTrace) {
      debugPrint('[PluginTester] Invocation failed: $e');
      debugPrintStack(stackTrace: stackTrace);

      _error = e.toString();
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
        });
      }
    }
  }

  /// Local invocation.
  ///
  /// Do not call PluginRuntime.invoke() here.
  /// The dispatcher resolves the capability through PluginRegistry,
  /// verifies plugin state, resolves the native artifact, and invokes
  /// the native runtime.
  Future<void> _invokeLocal() async {
    final result = await widget.dispatcher.invoke(
      capability: widget.capability.id,
      parameters: Map<String, dynamic>.from(_paramValues),
    );

    _result = _normalizeResult(result);
  }

  /// Remote invocation.
  ///
  /// Remote execution is handled by the backend/device.
  Future<void> _invokeRemote() async {
    final device = widget.device;

    if (device == null) {
      throw StateError('Remote device is missing.');
    }

    if (!device.connected) {
      throw StateError('${device.name} is offline.');
    }

    final response = await widget.manager.api.post(
      '/devices/${device.id}/invoke',
      {
        'capability': widget.capability.id,
        'parameters': Map<String, dynamic>.from(_paramValues),
        'timeout_seconds': 60,
      },
    );

    final raw = response['result'];

    _result = raw is Map
        ? Map<String, dynamic>.from(raw)
        : {'result': raw};
  }

  Map<String, dynamic> _normalizeResult(dynamic result) {
    if (result is Map<String, dynamic>) {
      return result;
    }

    if (result is Map) {
      return Map<String, dynamic>.from(result);
    }

    return {'result': result};
  }

  // ------------------------------------------------------------------
  // Result formatting
  // ------------------------------------------------------------------

  String _escapeNonAscii(String value) {
    final buffer = StringBuffer();

    for (final rune in value.runes) {
      if (rune >= 0x20 && rune <= 0x7E) {
        buffer.writeCharCode(rune);
      } else if (rune == 0x0A) {
        buffer.write(r'\n');
      } else if (rune == 0x09) {
        buffer.write(r'\t');
      } else if (rune == 0x0D) {
        buffer.write(r'\r');
      } else if (rune < 0x10000) {
        buffer.write(
          '<U+${rune.toRadixString(16).toUpperCase().padLeft(4, '0')}>',
        );
      } else {
        buffer.write('<U+${rune.toRadixString(16).toUpperCase()}>');
      }
    }

    return buffer.toString();
  }

  String _jsonEscaped(Map<String, dynamic> data) {
    final encoded = const JsonEncoder.withIndent('  ').convert(data);
    return _escapeNonAscii(encoded);
  }

  Widget _jsonBox(Object data) {
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Colors.grey.shade100,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.grey.shade300),
      ),
      child: SelectableText(
        data is String
            ? data
            : const JsonEncoder.withIndent('  ').convert(data),
        style: const TextStyle(fontSize: 12),
      ),
    );
  }

  Widget _errorBox(String message) {
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 6),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Colors.red.shade50,
        border: Border.all(color: Colors.red.shade200),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        message,
        style: const TextStyle(color: Colors.red),
      ),
    );
  }

  // ------------------------------------------------------------------
  // Header
  // ------------------------------------------------------------------

  Widget _header() {
    final device = widget.device;
    final titleSuffix = _isRemote && device != null
        ? ' on ${device.name}'
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
  // Metadata
  // ------------------------------------------------------------------

  Widget _metaSection() {
    if (_isRemote) {
      final device = widget.device!;

      final enabled = widget.manager.isEnabledOnDevice(
        widget.plugin.id,
        device.id,
      );

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
                    ? 'Plugin enabled on ${device.name}'
                    : 'Plugin disabled on ${device.name}',
                style: TextStyle(
                  color: enabled ? Colors.green : Colors.orange,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'device: ${device.id}',
            style: const TextStyle(
              fontSize: 12,
              fontFamily: 'monospace',
            ),
          ),
          Text(
            'platform: ${device.platform}/${device.architecture}',
            style: const TextStyle(
              fontSize: 12,
              fontFamily: 'monospace',
            ),
          ),
          _permissionChips(),
        ],
      );
    }

    final installed = widget.manager.isInstalled(widget.plugin.id);
    final enabled = widget.manager.isEnabled(widget.plugin.id);

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
              !installed
                  ? 'Plugin not installed'
                  : enabled
                      ? 'Plugin enabled'
                      : 'Plugin disabled',
              style: TextStyle(
                color: enabled ? Colors.green : Colors.orange,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          'device: ${widget.thisDeviceId}',
          style: const TextStyle(
            fontSize: 12,
            fontFamily: 'monospace',
          ),
        ),
        const SizedBox(height: 4),
        const Text(
          'Execution: CapabilityDispatcher → PluginRegistry → PluginRuntime',
          style: TextStyle(fontSize: 11),
        ),
        _permissionChips(),
      ],
    );
  }

  Widget _permissionChips() {
    final permissions = widget.capability.permissions;

    if (permissions.isEmpty) {
      return const SizedBox.shrink();
    }

    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Wrap(
        spacing: 6,
        runSpacing: 6,
        children: permissions.map((permission) {
          return Chip(
            label: Text(
              permission,
              style: const TextStyle(fontSize: 11),
            ),
            visualDensity: VisualDensity.compact,
            materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
          );
        }).toList(),
      ),
    );
  }

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
              child: ListView(
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
                  if (_result != null) ...[
                    const SizedBox(height: 12),
                    Row(
                      children: [
                        const Expanded(
                          child: Text(
                            'Result',
                            style: TextStyle(fontWeight: FontWeight.w700),
                          ),
                        ),
                        Switch(
                          value: _showCodepoints,
                          onChanged: (value) {
                            setState(() {
                              _showCodepoints = value;
                            });
                          },
                        ),
                        const Text(
                          'codepoints',
                          style: TextStyle(fontSize: 11),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    _jsonBox(
                      _showCodepoints
                          ? _jsonEscaped(_result!)
                          : _result!,
                    ),
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
  // Footer
  // ------------------------------------------------------------------

  Widget _footer() {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
        child: Row(
          children: [
            Expanded(
              child: OutlinedButton(
                onPressed: _busy
                    ? null
                    : () => Navigator.of(context).pop(),
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
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                        ),
                      )
                    : const Icon(Icons.play_arrow, size: 18),
                label: const Text('Invoke'),
                onPressed: _busy ? null : _invoke,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
