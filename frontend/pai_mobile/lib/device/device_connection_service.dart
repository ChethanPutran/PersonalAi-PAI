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

    return Uri(
      scheme: scheme,
      host: uri.host,
      port: uri.hasPort ? uri.port : null,
      path: '${uri.path}/devices/$deviceId/ws',
      queryParameters: params.isEmpty ? null : params,
    ).toString();
  }

  // -----------------------------------------------------------------
  // Connect / disconnect
  // -----------------------------------------------------------------

  Future<void> connect() async {
    if (_disposed) return;

    if (_state == DeviceConnectionState.connected ||
        _state == DeviceConnectionState.connecting) {
      return;
    }

    _manualDisconnect = false;
    _setState(DeviceConnectionState.connecting);

    try {
      final uri = Uri.parse(_webSocketUrl);
      print('[DeviceConnection] Connecting to $uri');

      final channel = WebSocketChannel.connect(uri);
      _channel = channel;

      await channel.ready;

      if (_disposed || _manualDisconnect) {
        await channel.sink.close();
        return;
      }

      _setState(DeviceConnectionState.connected);
      print('[DeviceConnection] Connected device=$deviceId');

      _startHeartbeat();

      channel.stream.listen(
        _handleMessage,
        onError: _handleError,
        onDone: _handleDisconnected,
        cancelOnError: false,
      );
    } catch (e) {
      print('[DeviceConnection] Connection failed: $e');
      _setState(DeviceConnectionState.disconnected);
      _scheduleReconnect();
    }
  }

  Future<void> disconnect() async {
    _manualDisconnect = true;

    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;

    _reconnectTimer?.cancel();
    _reconnectTimer = null;

    final channel = _channel;
    _channel = null;

    try {
      await channel?.sink.close();
    } catch (_) {}

    _setState(DeviceConnectionState.disconnected);
    print('[DeviceConnection] Disconnected device=$deviceId');
  }

  // -----------------------------------------------------------------
  // Outbound messages
  // -----------------------------------------------------------------

  Future<void> send(Map<String, dynamic> message) async {
    if (!isConnected || _channel == null) {
      throw StateError('Device is not connected');
    }
    final payload = jsonEncode(message);
    print('[DeviceConnection] Sending: $payload');
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
    try {
      print('[DeviceConnection] Received: $rawMessage');

      final Map<String, dynamic> message;

      if (rawMessage is String) {
        final decoded = jsonDecode(rawMessage);
        if (decoded is! Map) {
          print('[DeviceConnection] Invalid message');
          return;
        }
        message = Map<String, dynamic>.from(decoded);
      } else if (rawMessage is List<int>) {
        final decoded = jsonDecode(utf8.decode(rawMessage));
        if (decoded is! Map) return;
        message = Map<String, dynamic>.from(decoded);
      } else {
        print('[DeviceConnection] Unsupported message type: '
            '${rawMessage.runtimeType}');
        return;
      }

      final wire = message['type']?.toString();
      final type = DeviceMessageType.fromWire(wire);

      if (type == null) {
        print('[DeviceConnection] Unknown message type: $wire');
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
          print('[DeviceConnection] Unknown message type: $type');
          if (_messageHandler != null) {
            await _messageHandler!(message);
          }
      }
    } catch (e, stackTrace) {
      print('[DeviceConnection] Message handling error: $e');
      print(stackTrace);
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
      'type': DeviceMessageType.heartbeat.wire,
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
      print('[DeviceConnection] invoke: missing request_id or capability');
      return;
    }

    final handler = _invokeHandler;
    if (handler == null) {
      print('[DeviceConnection] invoke: no CapabilityInvokeHandler installed');
      await sendResult(
        requestId: requestId,
        success: false,
        error: 'Device has no capability handler installed',
      );
      return;
    }

    try {
      print('[DeviceConnection] invoke: $capability (req=$requestId)');
      final result = await handler(
        capability: capability,
        parameters: parameters,
      );
      await sendResult(
        requestId: requestId,
        success: true,
        result: result,
      );
      print('[DeviceConnection] → result for $requestId');
    } catch (e, st) {
      print('[DeviceConnection] invoke failed: $e\n$st');
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
      print('[DeviceConnection] control: missing request_id or unknown op '
          '(req=$requestId, op=${message['op']})');
      return;
    }

    print('[DeviceConnection] control ${op.wire} for req=$requestId');

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
      if (error != null) 'error': error,
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
        print('[DeviceConnection] plugin sync failed: $e');
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
    print('[DeviceConnection] WebSocket error: $error');
    _setState(DeviceConnectionState.disconnected);
    _scheduleReconnect();
  }

  void _handleDisconnected() {
    print('[DeviceConnection] WebSocket closed');

    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    _channel = null;

    _setState(DeviceConnectionState.disconnected);

    if (!_manualDisconnect && !_disposed) {
      _scheduleReconnect();
    }
  }

  void _startHeartbeat() {
    _heartbeatTimer?.cancel();

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
          print('[DeviceConnection] Heartbeat failed: $e');
        }
      },
    );
  }

  void _scheduleReconnect() {
    if (_disposed || _manualDisconnect || _reconnectTimer != null) {
      return;
    }

    _setState(DeviceConnectionState.reconnecting);
    print('[DeviceConnection] Reconnecting in 5 seconds...');

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
    print('[DeviceConnection] State: $_state');
  }

  void dispose() {
    _disposed = true;
    _manualDisconnect = true;

    _heartbeatTimer?.cancel();
    _reconnectTimer?.cancel();

    _heartbeatTimer = null;
    _reconnectTimer = null;

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