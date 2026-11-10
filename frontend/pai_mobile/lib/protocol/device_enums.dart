/// Wire-protocol enums for the device ⇄ backend WebSocket.
///
/// These are the Dart mirror of `pai/protocol/enums.py`. Keep them in
/// lock-step: the backend refuses unknown `type`/`op` values, and the
/// device silently drops frames it can't parse. A typo used to cause
/// a 10-minute debugging session; now it's a compile error.
library;

import 'dart:convert';


/// The `type` field on any WebSocket frame.
enum DeviceMessageType {
  // Lifecycle
  connected('connected'),
  ping('ping'),
  pong('pong'),
  heartbeat('heartbeat'),
  heartbeatAck('heartbeat_ack'),
  error('error'),

  command('command'),

  // Plugin sync
  syncPluginsRequest('sync_plugins_request'),
  syncPluginsResponse('sync_plugins_response'),

  // Capability invocation
  invoke('invoke'),
  result('result'),
  control('control'),
  controlAck('control_ack'),

  // Events
  event('event'),

  // Task / plan events
  taskCreated('task.created'),
  taskUpdated('task.updated'),
  taskStarted('task.started'),
  taskCompleted('task.completed'),
  taskFailed('task.failed'),
  taskPaused('task.paused'),
  taskResumed('task.resumed'),
  taskCancelled('task.cancelled'),
  planCreated('plan.created'),
  planCompleted('plan.completed'),
  planFailed('plan.failed');

  final String wire;
  const DeviceMessageType(this.wire);

  static DeviceMessageType? fromWire(String? s) {
    if (s == null) return null;
    for (final v in values) {
      if (v.wire == s) return v;
    }
    return null;
  }

  /// Task + plan events, used by TasksScreen's auto-refresh.
  bool get isTaskEvent => wire.startsWith('task.') || wire.startsWith('plan.');
}


/// The `op` field on a CONTROL frame.
enum DeviceControlOp {
  pause('pause'),
  resume('resume'),
  cancel('cancel');

  final String wire;
  const DeviceControlOp(this.wire);

  static DeviceControlOp? fromWire(String? s) {
    if (s == null) return null;
    for (final v in values) {
      if (v.wire == s) return v;
    }
    return null;
  }
}


/// Typed wrapper around a decoded frame — saves callers doing
/// `jsonDecode(raw) as Map<String, dynamic>` and guessing field names.
class DeviceFrame {
  final DeviceMessageType? type;
  final Map<String, dynamic> raw;

  DeviceFrame._(this.type, this.raw);

  static DeviceFrame? tryParse(dynamic payload) {
    Map<String, dynamic>? map;
    try {
      if (payload is String) {
        final decoded = jsonDecode(payload);
        if (decoded is Map) map = Map<String, dynamic>.from(decoded);
      } else if (payload is List<int>) {
        final decoded = jsonDecode(utf8.decode(payload));
        if (decoded is Map) map = Map<String, dynamic>.from(decoded);
      }
    } catch (_) {
      return null;
    }
    if (map == null) return null;
    return DeviceFrame._(
      DeviceMessageType.fromWire(map['type']?.toString()),
      map,
    );
  }

  // Convenience accessors used throughout DeviceConnectionService.
  String? get requestId   => raw['request_id']?.toString();
  String? get capability  => raw['capability']?.toString();
  String? get deviceId    => raw['device_id']?.toString();
  String? get eventName   => raw['event']?.toString();
  bool?   get ok          => raw['ok'] as bool?;
  String? get error       => raw['error']?.toString();

  DeviceControlOp? get controlOp =>
      DeviceControlOp.fromWire(raw['op']?.toString());

  Map<String, dynamic> get parameters =>
      (raw['parameters'] as Map?)?.cast<String, dynamic>() ?? {};

  Map<String, dynamic> get data =>
      (raw['data'] as Map?)?.cast<String, dynamic>() ?? {};
}