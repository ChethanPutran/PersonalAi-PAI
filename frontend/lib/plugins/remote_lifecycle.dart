import 'package:flutter/foundation.dart';

import '../device/remote_access.dart';

typedef FrameSender = Future<void> Function(Map<String, dynamic> frame);

typedef ReportInstall = Future<void> Function({
  required String pluginId,
  required String? version,
  String? operationId,
  required bool success,
  String? error,
});

typedef ReportUninstall = Future<void> Function({
  required String pluginId,
  String? operationId,
  required bool success,
  String? error,
});

/// The device's side of a plugin install/uninstall the backend asked for.
///
/// * Enforces the owner's switch: a request that did not come from this device needs
///   "Allow remote plugin install" (fail closed until the backend has said what is allowed).
/// * Reports progress (`plugin.progress` frames: downloading, verifying, installing,
///   awaiting_permission) and the final result (report-install / report-uninstall with the
///   operation id), so the requester sees every stage live and a failure in plain words.
class RemoteLifecycle {
  RemoteLifecycle({
    required this.thisDeviceId,
    required this.access,
    required this.send,
    required this.reportInstall,
    required this.reportUninstall,
    required this.bindStages,
  });

  final String? Function() thisDeviceId;
  final RemoteAccess Function() access;
  final FrameSender send;
  final ReportInstall reportInstall;
  final ReportUninstall reportUninstall;

  /// Hands the installer a listener for its stages (or `null` to detach).
  final void Function(void Function(String stage)? listener) bindStages;

  String? _activeOperation;

  static bool handles(String? type) => type == 'plugin.install' || type == 'plugin.uninstall';

  static String? pluginIdOf(Map<String, dynamic> m) {
    final plugin = m['plugin'];
    return (m['plugin_id'] ?? (plugin is Map ? plugin['id'] : null))?.toString();
  }

  static String? versionOf(Map<String, dynamic> m) {
    final plugin = m['plugin'];
    return (m['version'] ?? (plugin is Map ? plugin['version'] : null))?.toString();
  }

  /// Best effort: progress must never break the install itself.
  Future<void> progress(String? operationId, String status, {String? error, String? errorCode}) async {
    if (operationId == null || operationId.isEmpty) return;
    try {
      await send({
        'type': 'plugin.progress',
        'operation_id': operationId,
        'status': status,
        if (error != null) 'error': error,
        if (errorCode != null) 'error_code': errorCode,
      });
    } catch (e) {
      debugPrint('[RemoteLifecycle] progress "$status" not sent: $e');
    }
  }

  /// `null` = go ahead. Otherwise the command is refused: the failure was already reported to the
  /// requester (so it shows a clear reason) and the caller must not run it.
  Future<String?> refuseIfNotAllowed(Map<String, dynamic> message) async {
    final reason = remoteInstallRefusal(
      requestedBy: message['requested_by_device']?.toString(),
      thisDeviceId: thisDeviceId(),
      access: access(),
    );
    if (reason == null) return null;

    final op = message['operation_id']?.toString();
    final plugin = pluginIdOf(message);
    debugPrint('[RemoteLifecycle] refused ${message['type']} of $plugin: $reason');
    await progress(op, 'failed', error: reason, errorCode: 'remote_install_disabled');
    if (plugin != null) await _report(message, success: false, error: reason);
    return reason;
  }

  /// A command is about to run: remember its operation and forward the installer's stages.
  void begin(Map<String, dynamic> message) {
    _activeOperation = message['operation_id']?.toString();
    final op = _activeOperation;
    bindStages(op == null ? null : (stage) => progress(op, stage));
  }

  void end() {
    _activeOperation = null;
    bindStages(null);
  }

  /// The permission dialog is open / answered.
  Future<void> awaitingPermission() => progress(_activeOperation, 'awaiting_permission');
  Future<void> permissionAnswered() => progress(_activeOperation, 'installing');

  Future<void> succeeded(Map<String, dynamic> message) => _report(message, success: true);

  /// [error] is already user-readable or is mapped by the backend to a plain message.
  Future<void> failed(Map<String, dynamic> message, String error) async {
    await _report(message, success: false, error: error);
  }

  Future<void> _report(Map<String, dynamic> message, {required bool success, String? error}) async {
    final plugin = pluginIdOf(message);
    if (plugin == null) return;
    final op = message['operation_id']?.toString();
    if (message['type'] == 'plugin.uninstall') {
      await reportUninstall(pluginId: plugin, operationId: op, success: success, error: error);
    } else {
      await reportInstall(
          pluginId: plugin, version: versionOf(message), operationId: op, success: success, error: error);
    }
  }
}
