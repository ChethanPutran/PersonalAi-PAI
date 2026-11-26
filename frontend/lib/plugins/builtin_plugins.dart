// lib/plugins/builtin_plugins.dart
//
// Built-in plugins: provided by the backend itself (nothing to install on a device), switched on or
// off per user. The one that matters today is `agent`: it lets PAI work step by step toward a goal on
// the user's devices, including controlling the screen. It is OFF until the user turns it on here.
//
//   GET  /account/plugins            -> {plugins: [{id, name, description, version, builtin, enabled, ...}]}
//   POST /plugins/{id}/enable|disable (body {})

import 'package:flutter/foundation.dart';

import '../core/api/api_client.dart';

class BuiltinPlugin {
  final String id;
  final String name;
  final String description;
  final String version;
  final bool enabled;
  final bool requiresConfirmation;

  const BuiltinPlugin({
    required this.id,
    required this.name,
    required this.description,
    this.version = '',
    this.enabled = false,
    this.requiresConfirmation = false,
  });

  factory BuiltinPlugin.fromJson(Map<String, dynamic> j) => BuiltinPlugin(
        id: j['id']?.toString() ?? '',
        name: j['name']?.toString() ?? j['id']?.toString() ?? '',
        description: j['description']?.toString() ?? '',
        version: j['version']?.toString() ?? '',
        enabled: j['enabled'] == true,
        requiresConfirmation: j['requires_confirmation'] == true,
      );

  BuiltinPlugin copyWith({bool? enabled}) => BuiltinPlugin(
        id: id,
        name: name,
        description: description,
        version: version,
        enabled: enabled ?? this.enabled,
        requiresConfirmation: requiresConfirmation,
      );
}

/// Loads the built-in plugins and flips them. The switch moves at once and goes back, with a plain
/// message, if the server says no.
class BuiltinPluginsController extends ChangeNotifier {
  BuiltinPluginsController({required this.api});

  final ApiClient api;

  List<BuiltinPlugin> plugins = const [];
  bool loading = false;

  /// False until a load succeeded: the UI shows nothing for built-ins it could not fetch.
  bool loaded = false;

  /// Last failure, in words for the user (cleared by the next action).
  String? error;

  /// Plugins with a request in flight (their switch is disabled).
  final Set<String> busy = {};

  bool _disposed = false;

  Future<void> load() async {
    if (loading) return;
    loading = true;
    _notify();
    try {
      final res = await api.get('/account/plugins');
      final list = (res is Map ? res['plugins'] : null);
      plugins = [
        for (final p in (list is List ? list : const []))
          if (p is Map && p['builtin'] == true) BuiltinPlugin.fromJson(Map<String, dynamic>.from(p)),
      ];
      loaded = true;
      error = null;
    } catch (e) {
      debugPrint('[BuiltinPlugins] load failed: $e');
      // Keep what we had: a refresh that fails must not blank a working switch.
    } finally {
      loading = false;
      _notify();
    }
  }

  Future<void> setEnabled(String id, bool value) async {
    final i = plugins.indexWhere((p) => p.id == id);
    if (i < 0 || busy.contains(id)) return;
    final before = plugins[i];
    if (before.enabled == value) return;

    busy.add(id);
    error = null;
    plugins = [...plugins]..[i] = before.copyWith(enabled: value); // optimistic
    _notify();
    try {
      await api.post('/plugins/$id/${value ? 'enable' : 'disable'}', {});
    } catch (e) {
      debugPrint('[BuiltinPlugins] ${value ? 'enable' : 'disable'} $id failed: $e');
      final j = plugins.indexWhere((p) => p.id == id);
      if (j >= 0) plugins = [...plugins]..[j] = before; // roll back
      error = 'Could not turn ${before.name} ${value ? 'on' : 'off'}. ${_reason(e)}';
    } finally {
      busy.remove(id);
      _notify();
    }
  }

  static String _reason(Object e) {
    if (e is ApiException) {
      final b = e.body;
      final d = b is Map ? b['detail'] : null;
      if (d is String && d.isNotEmpty) return d;
      if (d is Map && d['message'] != null) return d['message'].toString();
      if (e.statusCode == 401 || e.statusCode == 403) return 'You are not allowed to change this.';
      return 'The server said no (${e.statusCode}). Try again.';
    }
    return 'Check your connection and try again.';
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
