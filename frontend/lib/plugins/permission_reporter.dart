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
  final Future<Map<String, String>?> Function(List<String> permissions) status;

  String? _lastSent;

  PermissionReporter({required this.api, required this.deviceId, required this.status});

  static const _valid = {'granted', 'denied', 'pending'};

  /// Reports for [declared] (installed plugin id -> permissions its manifest declares).
  /// Skips the call when nothing changed since the last successful report, unless [force].
  /// Never throws. Returns true if a report was sent.
  Future<bool> report(Map<String, List<String>> declared, {bool force = false}) async {
    try {
      final all = {for (final p in declared.values) ...p}.toList()..sort();
      if (all.isEmpty) return false;
      final answers = await status(all);
      if (answers == null) return false;

      final plugins = <String, Map<String, String>>{};
      declared.forEach((pluginId, perms) {
        final per = <String, String>{};
        for (final p in perms) {
          final a = answers[p];
          if (a != null && _valid.contains(a)) per[p] = a;
        }
        if (per.isNotEmpty) plugins[pluginId] = per;
      });
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
