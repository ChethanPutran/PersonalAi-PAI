import 'package:flutter/foundation.dart';

/// Where a remote plugin install/uninstall is, as reported by the backend (`plugin.operation`
/// frames and `GET /plugins/operations`).
@immutable
class PluginOperation {
  final String id;
  final String pluginId;
  final String deviceId;
  final String? version;
  final String kind; // install | uninstall
  final String status; // requested | delivered | downloading | verifying | installing | awaiting_permission | succeeded | failed
  final bool terminal;
  final String? errorCode;
  final String? errorMessage;
  final String? errorHint;
  final DateTime? updatedAt;

  const PluginOperation({
    required this.id,
    required this.pluginId,
    required this.deviceId,
    this.version,
    this.kind = 'install',
    required this.status,
    this.terminal = false,
    this.errorCode,
    this.errorMessage,
    this.errorHint,
    this.updatedAt,
  });

  bool get succeeded => status == 'succeeded';
  bool get failed => status == 'failed';

  /// `null` when [json] is not an operation (missing ids), so a malformed frame is ignored.
  static PluginOperation? tryParse(Object? json) {
    if (json is! Map) return null;
    final id = json['id']?.toString();
    final plugin = json['plugin_id']?.toString();
    final device = json['device_id']?.toString();
    final status = json['status']?.toString();
    if (id == null || plugin == null || device == null || status == null) return null;
    final err = json['error'];
    return PluginOperation(
      id: id,
      pluginId: plugin,
      deviceId: device,
      version: json['version']?.toString(),
      kind: json['kind']?.toString() ?? 'install',
      status: status,
      terminal: json['terminal'] == true || status == 'succeeded' || status == 'failed',
      errorCode: err is Map ? err['code']?.toString() : null,
      errorMessage: err is Map ? err['message']?.toString() : null,
      errorHint: err is Map ? err['hint']?.toString() : null,
      updatedAt: DateTime.tryParse(json['updated_at']?.toString() ?? ''),
    );
  }
}

/// What the user reads while a remote install runs.
String remoteStageLabel(String? status) {
  switch (status) {
    case 'requested':
    case 'delivered':
      return 'Waiting for the device…';
    case 'downloading':
      return 'Downloading…';
    case 'verifying':
      return 'Verifying…';
    case 'installing':
      return 'Installing…';
    case 'awaiting_permission':
      return 'Waiting for permission on the device';
    default:
      return 'Installing…';
  }
}

/// Plain-language text for an API failure of a remote request (never the raw exception).
/// [body] is the decoded error body; the backend sends `detail: {code, message}` or a string.
({String message, String? hint}) describeRemoteRequestError(Object? body) {
  String? code;
  String? message;
  if (body is Map) {
    final d = body['detail'];
    if (d is Map) {
      code = d['code']?.toString();
      message = d['message']?.toString();
    } else if (d is String) {
      message = d;
    }
  }
  String? hint;
  switch (code) {
    case 'remote_install_disabled':
      hint = 'Open Profile on that device and turn on "Allow remote plugin install".';
      break;
    case 'device_not_found':
      hint = 'Check the device is still on your account.';
      break;
  }
  return (message: message ?? "The request couldn't be sent.", hint: hint);
}
