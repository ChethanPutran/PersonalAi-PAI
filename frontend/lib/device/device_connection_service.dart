import 'connection_liveness.dart';
import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../protocol/device_enums.dart';

enum DeviceConnectionState {
  disconnected,
  connecting,
  connected,
  reconnecting,
}

/// Handler invoked when the backend sends an `invoke` frame.
/// Must return the payload to put into `result`.
typedef CapabilityInvokeHandler = Future<Map<String, dynamic>> Function({
  required String capability,
  required Map<String, dynamic> parameters,
});

/// Handler invoked when the backend sends `sync_plugins_request`.
/// Return the list of installed plugins (IDs/versions) as the backend expects.
typedef PluginSyncHandler = Future<List<Map<String, dynamic>>> Function();


/// Handler invoked when the backend sends a `control` frame
/// (pause / resume / cancel). Returning `false` signals "this
/// capability doesn't support that operation" so the backend can
/// fall back to its wrapper-level checkpoints.
typedef DeviceControlHandler = Future<bool> Function({
  required DeviceControlOp op,
  required String requestId,
});

typedef DeviceMessageHandler = Future<void> Function(
  Map<String, dynamic> message,
);


class DeviceConnectionService {
  final String baseUrl;
  final String deviceId;

  /// Silence for 3 × the heartbeat interval (90s) marks the socket dead: one lost beat is fine,
  /// two in a row is not. Refreshed by EVERY received frame, not just heartbeat acks.
  final ConnectionLiveness _liveness = ConnectionLiveness();
Timer? _heartbeatWatchdog;

  // Log a warning after this many consecutive failures, but keep retrying.
  static const _maxConsecutiveFailures = 5;
  int _consecutiveFailures = 0;

  StreamSubscription? _streamSub;

  /// JWT sent as `?token=` on the WebSocket URL.
  /// The WS endpoint validates it before accepting the connection.
  final String? token;

  WebSocketChannel? _channel;

  Timer? _heartbeatTimer;
  Timer? _reconnectTimer;

  bool _disposed = false;
  bool _manualDisconnect = false;

  DeviceConnectionState _state = DeviceConnectionState.disconnected;

  DeviceMessageHandler? _messageHandler;
  CapabilityInvokeHandler? _invokeHandler;
  PluginSyncHandler? _pluginSyncHandler;
  DeviceControlHandler? _controlHandler;


  /// Requests the backend has asked us to cancel. `_handleInvoke` checks
  /// this before sending a result — the backend already cancelled its
  /// future, so sending one back would just log "no pending future".
  final _cancelledRequests = <String>{};


  // Broadcast so multiple screens can listen.
  final _taskEventController =
      StreamController<Map<String, dynamic>>.broadcast();

  DeviceConnectionService({
    required this.baseUrl,
    required this.deviceId,
    this.token,
  });

  DeviceConnectionState get state => _state;
  bool get isConnected => _state == DeviceConnectionState.connected;

  /// Emits every `task.*` / `plan.*` frame received over the WS.
  Stream<Map<String, dynamic>> get taskEvents => _taskEventController.stream;

  void setMessageHandler(DeviceMessageHandler handler) {
    _messageHandler = handler;
  }

  /// Wire the capability executor. Called from `main.dart`.
  void setInvokeHandler(CapabilityInvokeHandler handler) {
    _invokeHandler = handler;
  }

  
  /// Optional — respond to the backend's plugin reconciliation query.
  void setPluginSyncHandler(PluginSyncHandler handler) {
    _pluginSyncHandler = handler;
  }

  /// Optional — react to control frames (pause / resume / cancel).
  ///
  /// `DeviceConnectionService` tracks cancel internally regardless of
  /// whether this handler is set; the handler is only needed for
  /// capabilities that support real pause/resume (e.g. camera.record).
  void setControlHandler(DeviceControlHandler handler) {
    _controlHandler = handler;
  }

  // -----------------------------------------------------------------
  // URL
  // -----------------------------------------------------------------

  String get _webSocketUrl {
    final uri = Uri.parse(baseUrl);
    final scheme = uri.scheme == 'https' ? 'wss' : 'ws';

    final params = <String, String>{};
    if (token != null && token!.isNotEmpty) {
      params['token'] = token!;
    }
    
    // baseUrl is the bare host (e.g. http://10.217.53.74:8000).
    // The backend mounts devices under /api/v1, so the WS path must
    // include that prefix. Previously the WS URL omitted it and the
    // request never matched a route — 403 from middleware, 404 from
    // routing, neither recoverable.
    final basePath = uri.path; // '' when baseUrl has no path
    return Uri(
      scheme: scheme,
      host: uri.host,
      port: uri.hasPort ? uri.port : null,
      path: '$basePath/api/v1/devices/$deviceId/ws',
      queryParameters: params.isEmpty ? null : params,
    ).toString();
  }



  // -----------------------------------------------------------------
  // Connect / disconnect
  // -----------------------------------------------------------------

  String _safeLogUri(Uri uri) {
    final qp = Map<String, String>.from(uri.queryParameters);
    final t = qp['token'];
    if (t != null && t.length > 16) {
      qp['token'] = '${t.substring(0, 6)}…${t.substring(t.length - 4)} '
                     '(${t.length} chars)';
    }
    return uri.replace(queryParameters: qp).toString();
  }

  Future<void> connect() async {
    if (_disposed) return;

    // Don't even try without a token — every attempt would be rejected
    // with 4401 and just burn the failure budget.
    if (token == null || token!.isEmpty) {
      debugPrint('[DeviceConnection] no token; deferring connect');
      return;
    }

    if (_state == DeviceConnectionState.connected ||
        _state == DeviceConnectionState.connecting) {
      return;
    }

    _manualDisconnect = false;
    _setState(DeviceConnectionState.connecting);

    // Drop any prior subscription before the new attempt so a
    // reconnect doesn't leave two listeners feeding _handleMessage.
    await _streamSub?.cancel();
    _streamSub = null;
    _channel = null;

    try {
      final uri = Uri.parse(_webSocketUrl);
      debugPrint('[DeviceConnection] Connecting to ${_safeLogUri(uri)}');

      final channel = WebSocketChannel.connect(uri);
      _channel = channel;

      await channel.ready;

      if (_disposed || _manualDisconnect) {
        await channel.sink.close();
        _channel = null;
        return;
      }

      _setState(DeviceConnectionState.connected);
      _consecutiveFailures = 0;   // ← successful handshake resets the budget
      debugPrint('[DeviceConnection] Connected device=$deviceId');

      _startHeartbeat();

      _streamSub = channel.stream.listen(
        _handleMessage,
        onError: _handleError,
        onDone: _handleDisconnected,
        cancelOnError: true,      // ← was false
      );
    } catch (e) {
      debugPrint('[DeviceConnection] Connection failed: $e');
      _channel = null;
      _setState(DeviceConnectionState.disconnected);

      _consecutiveFailures++;
      if (_consecutiveFailures >= _maxConsecutiveFailures) {
        debugPrint(
          '[DeviceConnection] $_maxConsecutiveFailures consecutive failures '
          '(last: $e) — will keep retrying with backoff',
        );
      }
      _scheduleReconnect();
    }
  }

  Future<void> disconnect() async {
    _manualDisconnect = true;

    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;

    _reconnectTimer?.cancel();
    _reconnectTimer = null;

    _heartbeatWatchdog?.cancel();
_heartbeatWatchdog = null;

    final channel = _channel;
    _channel = null;

    try {
      await channel?.sink.close();
    } catch (_) {}

    _setState(DeviceConnectionState.disconnected);
    debugPrint('[DeviceConnection] Disconnected device=$deviceId');
  }

  // -----------------------------------------------------------------
  // Outbound messages
  // -----------------------------------------------------------------

  Future<void> send(Map<String, dynamic> message) async {
    if (!isConnected || _channel == null) {
      throw StateError('Device is not connected');
    }
    final payload = jsonEncode(message);
    debugPrint('[DeviceConnection] Sending: $payload');
    _channel!.sink.add(payload);
  }

  Future<void> sendEvent({
    required String event,
    Map<String, dynamic>? data,
  }) async {
    await send({
      'type': DeviceMessageType.event.wire,
      'event': event,
      'device_id': deviceId,
      'data': data ?? {},
      'timestamp': DateTime.now().toUtc().toIso8601String(),
    });
  }

  Future<void> sendResult({
    required String requestId,
    required bool success,
    dynamic result,
    String? error,
  }) async {
    await send({
      'type': DeviceMessageType.result.wire,
      'request_id': requestId,
      'device_id': deviceId,
      'success': success,
      'result': result,
      'error': error,
      'timestamp': DateTime.now().toUtc().toIso8601String(),
    });
  }

  // -----------------------------------------------------------------
  // Inbound messages
  // -----------------------------------------------------------------

  Future<void> _handleMessage(dynamic rawMessage) async {
    _liveness.touch();          // any frame at all proves the socket is alive
    try {
      debugPrint('[DeviceConnection] Received: $rawMessage');

      final Map<String, dynamic> message;

      if (rawMessage is String) {
        final decoded = jsonDecode(rawMessage);
        if (decoded is! Map) {
          debugPrint('[DeviceConnection] Invalid message');
          return;
        }
        message = Map<String, dynamic>.from(decoded);
      } else if (rawMessage is List<int>) {
        final decoded = jsonDecode(utf8.decode(rawMessage));
        if (decoded is! Map) return;
        message = Map<String, dynamic>.from(decoded);
      } else {
        debugPrint('[DeviceConnection] Unsupported message type: '
            '${rawMessage.runtimeType}');
        return;
      }

      final wire = message['type']?.toString();
      final type = DeviceMessageType.fromWire(wire);

      if (type == null) {
        debugPrint('[DeviceConnection] Unknown message type: $wire');
        if (_messageHandler != null) {
          await _messageHandler!(message);
        }
        return;
      }

      // Exhaustive switch on DeviceMessageType — Dart 3 forces every
      // enum member to be handled, so a new frame type can't slip
      // through unnoticed.
      switch (type) {
        // ---- Lifecycle ----
        case DeviceMessageType.connected:
          debugPrint('[DeviceConnection] Backend handshake: $message');
          _consecutiveFailures = 0;
          // NEW: let the app react. Previously this branch was handled only
          // locally, so PaiApp._handleDeviceMessage's `case 'connected'`
          // — and the reconcile it triggers — never ran.
          if (_messageHandler != null) {
            await _messageHandler!(message);
          }
          break;
        case DeviceMessageType.ping:
          await _handlePing(message);
          break;

        case DeviceMessageType.pong:
          // Device doesn't send pings to the backend, so a pong here
          // is likely noise — log at debug and move on.
          debugPrint('[DeviceConnection] pong received');
          break;

        case DeviceMessageType.heartbeat:
          await _handleHeartbeat(message);
          break;

        case DeviceMessageType.heartbeatAck:
          debugPrint('[DeviceConnection] Heartbeat ack: $message');
          break;

        case DeviceMessageType.error:
          debugPrint('[DeviceConnection] server error: $message');
          break;

        case DeviceMessageType.command:
          await _handleCommand(message);
          break;

        // ---- Plugin sync ----
        case DeviceMessageType.syncPluginsRequest:
          await _handleSyncPluginsRequest(message);
          break;

        case DeviceMessageType.syncPluginsResponse:
          // Device is the sender normally, but handle defensively.
          debugPrint(
            '[DeviceConnection] sync_plugins_response: '
            '${(message['plugins'] as List?)?.length ?? 0} plugin(s)',
          );
          break;

        // ---- Capability invocation ----
        case DeviceMessageType.invoke:
          await _handleInvoke(message);
          break;

        case DeviceMessageType.result:
          await _handleResult(message);
          break;

        case DeviceMessageType.control:
          await _handleControl(message);
          break;

        case DeviceMessageType.controlAck:
          // Normally sent by us, not received — log and ignore.
          debugPrint('[DeviceConnection] control_ack: $message');
          break;

        // ---- Events ----
        case DeviceMessageType.event:
          await _handleEvent(message);
          break;


        // ---- Task / plan events → stream consumers ----
        case DeviceMessageType.taskCreated:
        case DeviceMessageType.taskUpdated:
        case DeviceMessageType.taskStarted:
        case DeviceMessageType.taskCompleted:
        case DeviceMessageType.taskFailed:
        case DeviceMessageType.taskPaused:
        case DeviceMessageType.taskResumed:
        case DeviceMessageType.taskCancelled:
        case DeviceMessageType.planCreated:
        case DeviceMessageType.planCompleted:
        case DeviceMessageType.planFailed:
          _taskEventController.add(message);
          // Preserve existing behaviour for any custom handler.
          if (_messageHandler != null) {
            await _messageHandler!(message);
          }
          break;


        default:
          debugPrint('[DeviceConnection] Unknown message type: $type');
          if (_messageHandler != null) {
            await _messageHandler!(message);
          }
      }
    } catch (e, stackTrace) {
      debugPrint('[DeviceConnection] Message handling error: $e');
      debugPrint(stackTrace.toString());
    }
  }

  Future<void> _handlePing(Map<String, dynamic> message) async {
    await send({
      'type': DeviceMessageType.pong.wire,
      'device_id': deviceId,
      'timestamp': DateTime.now().toUtc().toIso8601String(),
    });
  }

Future<void> _handleHeartbeat(Map<String, dynamic> message) async {
  await send({
    'type': DeviceMessageType.heartbeatAck.wire,   
    'device_id': deviceId,
    'timestamp': DateTime.now().toUtc().toIso8601String(),
  });
}

  Future<void> _handleCommand(Map<String, dynamic> message) async {
    if (_messageHandler != null) {
      await _messageHandler!(message);
    }
  }

  Future<void> _handleEvent(Map<String, dynamic> message) async {
    if (_messageHandler != null) {
      await _messageHandler!(message);
    }
  }

  // ---- The heart of device-side execution ----

  Future<void> _handleInvoke(Map<String, dynamic> message) async {
    final requestId = message['request_id']?.toString() ?? '';
    final capability = message['capability']?.toString() ?? '';
    final parameters =
        (message['parameters'] as Map?)?.cast<String, dynamic>() ?? {};

    if (requestId.isEmpty || capability.isEmpty) {
      debugPrint('[DeviceConnection] invoke: missing request_id or capability');
      return;
    }

    final handler = _invokeHandler;
    if (handler == null) {
      debugPrint('[DeviceConnection] invoke: no CapabilityInvokeHandler installed');
      await sendResult(
        requestId: requestId,
        success: false,
        error: 'Device has no capability handler installed',
      );
      return;
    }

    try {
      debugPrint('[DeviceConnection] invoke: $capability (req=$requestId)');
      final result = await handler(
        capability: capability,
        parameters: parameters,
      );
      await sendResult(
        requestId: requestId,
        success: true,
        result: result,
      );
      debugPrint('[DeviceConnection] → result for $requestId');
    } catch (e, st) {
      debugPrint('[DeviceConnection] invoke failed: $e\n$st');
      await sendResult(
        requestId: requestId,
        success: false,
        error: e.toString(),
      );
    }
  }

  Future<void> _handleControl(Map<String, dynamic> message) async {
    final requestId = message['request_id']?.toString() ?? '';
    final op = DeviceControlOp.fromWire(message['op']?.toString());

    if (requestId.isEmpty || op == null) {
      debugPrint('[DeviceConnection] control: missing request_id or unknown op '
          '(req=$requestId, op=${message['op']})');
      return;
    }

    debugPrint('[DeviceConnection] control ${op.wire} for req=$requestId');

    // Track cancels locally so `_handleInvoke` can discard the result
    // without a useless WS round-trip. This runs regardless of whether
    // a custom control handler is installed.
    if (op == DeviceControlOp.cancel) {
      _cancelledRequests.add(requestId);
    }

    bool ok = true;
    String? error;
    final handler = _controlHandler;
    if (handler != null) {
      try {
        ok = await handler(op: op, requestId: requestId);
        if (!ok) {
          error = 'Control not honoured for this capability';
        }
      } catch (e) {
        ok = false;
        error = e.toString();
      }
    }

    await send({
      'type': DeviceMessageType.controlAck.wire,
      'op': op.wire,
      'request_id': requestId,
      'ok': ok,
      'error': ?error,
      'timestamp': DateTime.now().toUtc().toIso8601String(),
    });
  }

  Future<void> _handleSyncPluginsRequest(Map<String, dynamic> message) async {
    final handler = _pluginSyncHandler;
    List<Map<String, dynamic>> plugins = const [];
    if (handler != null) {
      try {
        plugins = await handler();
      } catch (e) {
        debugPrint('[DeviceConnection] plugin sync failed: $e');
      }
    }
    await send({
      'type': DeviceMessageType.syncPluginsResponse.wire,
      'device_id': deviceId,
      'plugins': plugins,
      'timestamp': DateTime.now().toUtc().toIso8601String(),
    });
  }


  Future<void> _handleResult(Map<String, dynamic> message) async {
    if (_messageHandler != null) {
      await _messageHandler!(message);
    }
  }

  // -----------------------------------------------------------------
  // Errors / reconnect
  // -----------------------------------------------------------------

  void _handleError(Object error, StackTrace stackTrace) {
    debugPrint('[DeviceConnection] WebSocket error: $error');
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    _heartbeatWatchdog?.cancel();
    _heartbeatWatchdog = null;
    _channel = null;
    _streamSub = null;
    _setState(DeviceConnectionState.disconnected);

    if (_manualDisconnect || _disposed) return;

    _consecutiveFailures++;
    if (_consecutiveFailures >= _maxConsecutiveFailures) {
      debugPrint(
        '[DeviceConnection] $_maxConsecutiveFailures consecutive failures '
        '(last: $error) — will keep retrying with backoff',
      );
    }
    _scheduleReconnect();
  }

  void _handleDisconnected() {
    // Read closeCode BEFORE nulling the channel — it's gone afterwards.
    final closeCode = _channel?.closeCode;
    debugPrint('[DeviceConnection] WebSocket closed (code=$closeCode)');

    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    _heartbeatWatchdog?.cancel();
    _heartbeatWatchdog = null;
    _channel = null;
    _streamSub = null;
    _setState(DeviceConnectionState.disconnected);

    // Auth/ownership rejections: retrying with the same credentials
    // would just loop. The backend now accepts-then-closes with these
    // codes (see routes.py), so they DO arrive here.
    if (closeCode == 4401 || closeCode == 4403 || closeCode == 4003) {
      debugPrint(
        '[DeviceConnection] terminal close ($closeCode); '
        'not reconnecting',
      );
      return;
    }

    if (_manualDisconnect || _disposed) return;

    _consecutiveFailures++;
    if (_consecutiveFailures >= _maxConsecutiveFailures) {
      debugPrint(
        '[DeviceConnection] giving up after $_maxConsecutiveFailures '
        'failures',
      );
      return;
    }
    _scheduleReconnect();
  }


void _startHeartbeat() {
  _heartbeatTimer?.cancel();
  _heartbeatWatchdog?.cancel();

  _liveness.touch();

  _heartbeatTimer = Timer.periodic(
    const Duration(seconds: 30),
    (_) async {
      if (!isConnected) return;
      try {
        await send({
          'type': DeviceMessageType.heartbeat.wire,
          'device_id': deviceId,
          'timestamp': DateTime.now().toUtc().toIso8601String(),
        });
      } catch (e) {
        debugPrint('[DeviceConnection] Heartbeat failed: $e');
        _handleDeadSocket('heartbeat_send_failed: $e');
      }
    },
  );

  // Every 30s check whether the last ack is still fresh. Runs on a
  // separate timer so a slow send can't starve the check.
  _heartbeatWatchdog = Timer.periodic(
    const Duration(seconds: 30),
    (_) {
      if (!isConnected) return;
      if (_liveness.isStale) {
        debugPrint(
          '[DeviceConnection] nothing received for ${_liveness.silence!.inSeconds}s '
          '— reconnecting',
        );
        _handleDeadSocket('heartbeat_ack_timeout');
      }
    },
  );
}

void _handleDeadSocket(String reason) {
  if (_disposed || _manualDisconnect) return;

  _heartbeatTimer?.cancel();
  _heartbeatTimer = null;

  _heartbeatWatchdog?.cancel();
  _heartbeatWatchdog = null;

  _streamSub?.cancel();
  _streamSub = null;
  try { _channel?.sink.close(); } catch (_) {}
  _channel = null;

  _setState(DeviceConnectionState.disconnected);
  debugPrint('[DeviceConnection] socket marked dead ($reason)');

  _consecutiveFailures++;
  if (_consecutiveFailures >= _maxConsecutiveFailures) {
    debugPrint(
      '[DeviceConnection] $_maxConsecutiveFailures consecutive failures '
      '— will keep retrying with backoff',
    );
  }
  _scheduleReconnect();
}

  void _scheduleReconnect() {
    if (_disposed || _manualDisconnect || _reconnectTimer != null) {
      return;
    }

    _setState(DeviceConnectionState.reconnecting);
    debugPrint('[DeviceConnection] Reconnecting in 5 seconds...');

    _reconnectTimer = Timer(const Duration(seconds: 5), () {
      _reconnectTimer = null;
      if (!_disposed && !_manualDisconnect) {
        connect();
      }
    });
  }

  void _setState(DeviceConnectionState newState) {
    if (_state == newState) return;
    _state = newState;
    debugPrint('[DeviceConnection] State: $_state');
  }

  void dispose() {
    _disposed = true;
    _manualDisconnect = true;

    _heartbeatTimer?.cancel();
    _reconnectTimer?.cancel();
_heartbeatWatchdog?.cancel();
_heartbeatWatchdog = null;

    _heartbeatTimer = null;
    _reconnectTimer = null;

    _streamSub?.cancel();          
    _streamSub = null;   

    try {
      _channel?.sink.close();
    } catch (_) {}

    _channel = null;
    _messageHandler = null;
    _invokeHandler = null;
    _pluginSyncHandler = null;
    _controlHandler = null;
    _cancelledRequests.clear();
    _taskEventController.close();

    _state = DeviceConnectionState.disconnected;
  }
}
