import 'package:flutter/foundation.dart';

import '../core/api/api_client.dart';

/// Tells the backend which permissions the user allowed ON THIS DEVICE for each installed plugin.
///
/// Advisory only: the device re-checks before every invoke. The server uses the report to stop offering a
/// capability this device would refuse, and to explain why. Where the platform cannot answer without
/// prompting ([status] returns null), nothing is reported: unknown never blocks anything.
class PermissionReporter {
  final ApiClient api;
  final String deviceId;

  /// Read-only permission query (see PluginRuntime.permissionStatus). Null = this platform can't say.
  /// Asked per plugin ([nativeModule]): each plugin's decisions are its own.
  final Future<Map<String, String>?> Function(String nativeModule, List<String> permissions) status;

  String? _lastSent;

  PermissionReporter({required this.api, required this.deviceId, required this.status});

  static const _valid = {'granted', 'denied', 'pending'};

  /// Reports for [declared] (installed plugin id -> permissions its manifest declares).
  /// Skips the call when nothing changed since the last successful report, unless [force].
  /// Never throws. Returns true if a report was sent.
  ///
  /// [nativeModules] maps a plugin id to the native module its decisions are stored under (defaults to the id).
  Future<bool> report(
    Map<String, List<String>> declared, {
    Map<String, String> nativeModules = const {},
    bool force = false,
  }) async {
    try {
      final plugins = <String, Map<String, String>>{};
      for (final entry in declared.entries) {
        final pluginId = entry.key;
        final perms = entry.value;
        if (perms.isEmpty) continue;
        final answers = await status(nativeModules[pluginId] ?? pluginId, perms);
        if (answers == null) continue; // this platform cannot say: unknown, never granted or denied
        final per = <String, String>{};
        for (final p in perms) {
          final a = answers[p];
          if (a != null && _valid.contains(a)) per[p] = a;
        }
        if (per.isNotEmpty) plugins[pluginId] = per;
      }
      if (plugins.isEmpty) return false;

      final key = _canonical(plugins);
      if (!force && key == _lastSent) return false;
      await api.post('/plugins/device/$deviceId/permissions', {'plugins': plugins});
      _lastSent = key;
      return true;
    } catch (e) {
      debugPrint('[PermissionReporter] report failed: $e');
      return false;
    }
  }

  /// Forget what was sent, so the next report goes out (e.g. after the server may have lost it).
  void reset() => _lastSent = null;

  static String _canonical(Map<String, Map<String, String>> m) {
    final ids = m.keys.toList()..sort();
    return ids.map((i) {
      final ps = m[i]!.keys.toList()..sort();
      return '$i:${ps.map((p) => '$p=${m[i]![p]}').join(',')}';
    }).join(';');
  }
}
