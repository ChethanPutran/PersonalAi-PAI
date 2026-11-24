
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:pai_mobile/models/device.dart';
import 'package:pai_mobile/plugins/models/plugin_capability.dart';

import '../../plugins/models/plugin_info.dart';
import '../../plugins/plugin_manager.dart';
import '../../plugins/capability_dispatcher.dart';
import '../../plugins/permissions/permission_models.dart';
import '../../plugins/permissions/permissions_service.dart';
import 'permissions/confirmation_dialog.dart';
import 'permissions/permissions_screen.dart';
import 'widgets/param_field.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';

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

  /// Server permission checks / confirmation. Defaults to the app-wide instance.
  final PermissionsService? permissions;

  const PluginTestScreen({
    super.key,
    required this.manager,
    required this.dispatcher,
    required this.thisDeviceId,
    this.plugin,
    required this.device,
    this.remoteCapabilities = const [],
    this.permissions,
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

  PermissionsService get _permissions =>
      widget.permissions ?? PermissionsService.of(widget.manager.api);

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

    return HudScaffold(
      appBar: HudAppBar(
        titleWidget: Text(
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
      color: PaiColors.amber.withValues(alpha: 0.10),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: [
            Icon(Icons.cloud_off, color: PaiColors.amber),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                '${device.name} is offline. '
                'Invocations will fail until the device reconnects.',
                style: TextStyle(
                  fontSize: 12,
                  color: PaiColors.amber,
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

    return HudScaffold(
      appBar: HudAppBar(              
        titleWidget: const Text('Plugin Tester'),
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
              ? PaiColors.red
              : enabled
                  ? PaiColors.green
                  : PaiColors.amber,
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
      builder: (_) => CapabilityTestSheet(
        manager: widget.manager,
        dispatcher: widget.dispatcher,
        plugin: plugin,
        capability: capability,
        device: widget.device,
        thisDeviceId: widget.thisDeviceId,
        permissions: _permissions,
      ),
    );
  }
}

// ==========================================================================
// Capability test bottom sheet
// ==========================================================================

class CapabilityTestSheet extends StatefulWidget {
  final PluginManager manager;
  final CapabilityDispatcher dispatcher;
  final PluginInfo plugin;
  final PluginCapability capability;
  final DeviceInfo? device;
  final String thisDeviceId;
  final PermissionsService permissions;

  const CapabilityTestSheet({
    super.key,
    required this.manager,
    required this.dispatcher,
    required this.plugin,
    required this.capability,
    required this.thisDeviceId,
    required this.device,
    required this.permissions,
  });

  @override
  State<CapabilityTestSheet> createState() =>
      _CapabilityTestSheetState();
}

class _CapabilityTestSheetState extends State<CapabilityTestSheet> {
  bool get _isRemote =>
      widget.device != null &&
      widget.device!.id != widget.thisDeviceId;

  final Map<String, dynamic> _paramValues = {};

  bool _busy = false;
  bool _showCodepoints = false;

  /// Shown while we wait on the server ("Checking permissions…", "Waiting for the device…").
  String? _status;

  String? _error;
  Map<String, dynamic>? _result;

  @override
  void initState() {
    super.initState();
    _seedParamDefaults();
    // Warm the "am I an admin?" answer now so it is known even if the server drops later.
    unawaited(widget.permissions.isAdmin());
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
      _status = null;
    });

    try {
      if (_isRemote) {
        await _invokeRemote();
      } else {
        await _invokeLocal();
      }
    } on TimeoutException catch (e) {
      debugPrint('[PluginTester] Gave up waiting: $e');
      _error = 'Still running after 60 s — it may still finish. Check the Tasks screen.';
    } catch (e, stackTrace) {
      debugPrint('[PluginTester] Invocation failed: $e');
      debugPrintStack(stackTrace: stackTrace);

      _error = e.toString();
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _status = null;
        });
      }
    }
  }

  void _setStatus(String? message) {
    if (mounted) setState(() => _status = message);
  }

  /// Local invocation.
  ///
  /// Nothing runs natively before the server has said it may: the server decides (allow /
  /// confirm / deny) exactly as it does for chat, plans and schedules. If no decision can be
  /// obtained at all, only an admin may run it; everyone else is refused.
  Future<void> _invokeLocal() async {
    final params = Map<String, dynamic>.from(_paramValues);
    _setStatus('Checking permissions…');

    final PreflightResult decision;
    try {
      decision = await widget.permissions.preflight(
        deviceId: widget.thisDeviceId,
        capability: widget.capability.id,
        parameters: params,
      );
    } on PreflightUnavailable catch (e) {
      final admin = widget.permissions.cachedIsAdmin ?? await widget.permissions.isAdmin();
      if (admin) {
        debugPrint('[PluginTester] No permission decision (${e.reason}); running because the user is an admin.');
        await _runNative(params);
        return;
      }
      _error = 'Can’t check permissions right now (${e.reason}). Connect to the server and try '
          'again — only admins can run actions without a permission check.';
      return;
    }

    switch (decision.decision) {
      case PreflightDecision.allow:
        await _runNative(params);
      case PreflightDecision.deny:
        _error = decision.reason?.isNotEmpty == true
            ? 'Not allowed: ${decision.reason}'
            : 'This action is not allowed.';
      case PreflightDecision.confirm:
        await _confirmThenRunViaServer(decision);
    }
  }

  Future<void> _runNative(Map<String, dynamic> params) async {
    _setStatus(null);
    final result = await widget.dispatcher.invoke(
      capability: widget.capability.id,
      parameters: params,
    );
    _result = _normalizeResult(result);
  }

  /// The action needs approval. A natively-started run has no server task to approve, so the
  /// run goes through the server instead (`/devices/{this device}/invoke`), which creates the
  /// task, asks the same approval dialog, and executes it on this device.
  Future<void> _confirmThenRunViaServer(PreflightResult decision) async {
    final device = widget.device;
    if (device == null || !device.connected) {
      _error = 'This action needs your approval, and this device is not connected to the '
          'server to ask for it. Connect and try again.';
      return;
    }
    _setStatus('Asking for approval…');
    final outcome = await widget.permissions.invoke(
      deviceId: widget.thisDeviceId,
      capability: widget.capability.id,
      parameters: Map<String, dynamic>.from(_paramValues),
    );
    await _completeRun(
      outcome,
      deviceName: device.name,
      // Local runs offer "once" and "this session" only.
      limitScopes: const {ConfirmationScope.once, ConfirmationScope.session},
    );
  }

  /// Remote invocation.
  ///
  /// Goes through the server's normal task pipeline, so authorization, confirmation and audit
  /// all apply. Admins are never prompted; everyone else is asked via the approval dialog.
  Future<void> _invokeRemote() async {
    final device = widget.device;

    if (device == null) {
      throw StateError('Remote device is missing.');
    }

    if (!device.connected) {
      throw StateError('${device.name} is offline.');
    }

    final outcome = await widget.permissions.invoke(
      deviceId: device.id,
      capability: widget.capability.id,
      parameters: Map<String, dynamic>.from(_paramValues),
      timeoutSeconds: 60,
    );
    await _completeRun(outcome, deviceName: device.name);
  }

  /// Shared tail of both server-run paths: approval (if asked), then the result.
  Future<void> _completeRun(
    InvokeOutcome outcome, {
    required String deviceName,
    Set<ConfirmationScope>? limitScopes,
  }) async {
    final taskId = outcome.taskId;

    if (outcome.awaitingConfirmation) {
      if (taskId == null) {
        throw StateError('The server asked for approval but did not say which task.');
      }
      _setStatus(null);
      final request = (outcome.confirmation ??
              ConfirmationRequest(capability: widget.capability.id))
          .withDeviceName(deviceName);
      final approved = await showConfirmationDialog(
        context,
        request: request,
        limitScopes: limitScopes,
        onConfirm: (decision) => widget.permissions.confirmTask(taskId, decision),
        onDeny: () => widget.permissions.denyTask(taskId),
      );
      if (!mounted) return;
      if (!approved) {
        _error = 'Denied — nothing was run.';
        return;
      }
      _setStatus('Waiting for the device…');
      _applyState(await widget.permissions.waitForTask(taskId));
      return;
    }

    if (outcome.finished) {
      _applyOutcome(outcome);
      return;
    }

    if (taskId != null) {
      _setStatus('Waiting for the device…');
      _applyState(await widget.permissions.waitForTask(taskId));
      return;
    }

    _result = _normalizeResult(outcome.result);
  }

  void _applyOutcome(InvokeOutcome o) {
    if (o.status == 'failed' || o.status == 'cancelled') {
      _error = o.error ?? (o.status == 'cancelled' ? 'The task was cancelled.' : 'The action failed.');
      return;
    }
    _result = _normalizeResult(o.result);
  }

  void _applyState(TaskRunState state) {
    if (state.succeeded) {
      _result = _normalizeResult(state.output);
    } else if (state.status == 'cancelled') {
      _error = 'The task was cancelled.';
    } else {
      _error = state.error ?? 'The action failed.';
    }
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
        color: PaiColors.bg2,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: context.pai.line),
      ),
      child: SelectableText(
        data is String
            ? data
            : const JsonEncoder.withIndent('  ').convert(data),
        style: const TextStyle(fontSize: 12),
      ),
    );
  }

  /// Calm "something is happening on the server/device" line (not an error).
  Widget _statusRow(String message) {
    return Padding(
      key: const Key('tester-status'),
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(children: [
        const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
        const SizedBox(width: 10),
        Expanded(child: Text(message, style: TextStyle(color: context.pai.muted))),
      ]),
    );
  }

  Widget _errorBox(String message) {
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 6),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: PaiColors.red.withValues(alpha: 0.10),
        border: Border.all(color: PaiColors.red.withValues(alpha: 0.45)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        message,
        style: const TextStyle(color: PaiColors.red),
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
                      style: TextStyle(color: context.pai.muted),
                    ),
                  ),
              ],
            ),
          ),
          IconButton(
            key: const Key('open-permissions'),
            tooltip: 'Permissions',
            icon: const Icon(Icons.verified_user_outlined),
            onPressed: _busy ? null : _openPermissions,
          ),
          IconButton(
            icon: const Icon(Icons.close),
            onPressed: () => Navigator.of(context).pop(),
          ),
        ],
      ),
    );
  }

  void _openPermissions() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => PermissionsScreen(
          service: widget.permissions,
          deviceNames: {
            if (widget.device != null) widget.device!.id: widget.device!.name,
          },
        ),
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
                color: enabled ? PaiColors.green : PaiColors.amber,
              ),
              const SizedBox(width: 4),
              Text(
                enabled
                    ? 'Plugin enabled on ${device.name}'
                    : 'Plugin disabled on ${device.name}',
                style: TextStyle(
                  color: enabled ? PaiColors.green : PaiColors.amber,
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
              color: enabled ? PaiColors.green : PaiColors.amber,
            ),
            const SizedBox(width: 4),
            Text(
              !installed
                  ? 'Plugin not installed'
                  : enabled
                      ? 'Plugin enabled'
                      : 'Plugin disabled',
              style: TextStyle(
                color: enabled ? PaiColors.green : PaiColors.amber,
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
                    Text(
                      'No parameters.',
                      style: TextStyle(
                        fontSize: 12,
                        color: context.pai.muted,
                      ),
                    )
                  else
                    ..._paramValues.keys.map(_buildParamField),
                  const SizedBox(height: 8),
                  if (_status != null) _statusRow(_status!),
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
