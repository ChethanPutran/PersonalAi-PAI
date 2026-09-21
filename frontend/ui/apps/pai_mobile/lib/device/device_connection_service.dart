import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

enum DeviceConnectionState {
  disconnected,
  connecting,
  connected,
  reconnecting,
}

typedef DeviceMessageHandler = Future<void> Function(
  Map<String, dynamic> message,
);

class DeviceConnectionService {
  final String baseUrl;
  final String deviceId;

  WebSocketChannel? _channel;

  Timer? _heartbeatTimer;
  Timer? _reconnectTimer;

  bool _disposed = false;
  bool _manualDisconnect = false;

  DeviceConnectionState _state =
      DeviceConnectionState.disconnected;

  DeviceMessageHandler? _messageHandler;

  DeviceConnectionService({
    required this.baseUrl,
    required this.deviceId,
  });

  DeviceConnectionState get state => _state;

  bool get isConnected =>
      _state == DeviceConnectionState.connected;

  void setMessageHandler(DeviceMessageHandler handler) {
    _messageHandler = handler;
  }

  String get _webSocketUrl {
    final uri = Uri.parse(baseUrl);

    final scheme =
        uri.scheme == 'https' ? 'wss' : 'ws';

    return Uri(
      scheme: scheme,
      host: uri.host,
      port: uri.hasPort ? uri.port : null,
      path: '${uri.path}/devices/$deviceId/ws',
    ).toString();
  }

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

      print(
        '[DeviceConnection] Connecting to $uri',
      );

      final channel =
          WebSocketChannel.connect(uri);

      _channel = channel;

      await channel.ready;

      if (_disposed || _manualDisconnect) {
        await channel.sink.close();
        return;
      }

      _setState(DeviceConnectionState.connected);

      print(
        '[DeviceConnection] Connected '
        'device=$deviceId',
      );

      _startHeartbeat();

      channel.stream.listen(
        _handleMessage,
        onError: _handleError,
        onDone: _handleDisconnected,
        cancelOnError: false,
      );
    } catch (e) {
      print(
        '[DeviceConnection] Connection failed: $e',
      );

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

    print(
      '[DeviceConnection] Disconnected '
      'device=$deviceId',
    );
  }

  Future<void> send(
    Map<String, dynamic> message,
  ) async {
    if (!isConnected || _channel == null) {
      throw StateError(
        'Device is not connected',
      );
    }

    final payload = jsonEncode(message);

    print(
      '[DeviceConnection] Sending: $payload',
    );

    _channel!.sink.add(payload);
  }

  Future<void> sendEvent({
    required String event,
    Map<String, dynamic>? data,
  }) async {
    await send({
      'type': 'event',
      'event': event,
      'device_id': deviceId,
      'data': data ?? {},
      'timestamp':
          DateTime.now().toUtc().toIso8601String(),
    });
  }

  Future<void> sendResult({
    required String requestId,
    required bool success,
    dynamic result,
    String? error,
  }) async {
    await send({
      'type': 'result',
      'request_id': requestId,
      'device_id': deviceId,
      'success': success,
      'result': result,
      'error': error,
      'timestamp':
          DateTime.now().toUtc().toIso8601String(),
    });
  }

  Future<void> _handleMessage(
    dynamic rawMessage,
  ) async {
    try {
      print(
        '[DeviceConnection] Received: $rawMessage',
      );

      final Map<String, dynamic> message;

      if (rawMessage is String) {
        final decoded = jsonDecode(rawMessage);

        if (decoded is! Map) {
          print(
            '[DeviceConnection] Invalid message',
          );
          return;
        }

        message =
            Map<String, dynamic>.from(decoded);
      } else if (rawMessage is List<int>) {
        final decoded =
            jsonDecode(utf8.decode(rawMessage));

        if (decoded is! Map) {
          return;
        }

        message =
            Map<String, dynamic>.from(decoded);
      } else {
        print(
          '[DeviceConnection] Unsupported message '
          'type: ${rawMessage.runtimeType}',
        );
        return;
      }

      final type = message['type'];

      switch (type) {
        case 'ping':
          await _handlePing(message);
          break;

        case 'command':
          await _handleCommand(message);
          break;

        case 'event':
          await _handleEvent(message);
          break;

        case 'result':
          await _handleResult(message);
          break;

        case 'heartbeat':
          await _handleHeartbeat(message);
          break;

        default:
          print(
            '[DeviceConnection] Unknown message type: '
            '$type',
          );

          if (_messageHandler != null) {
            await _messageHandler!(message);
          }
      }
    } catch (e, stackTrace) {
      print(
        '[DeviceConnection] Message handling error: '
        '$e',
      );

      print(stackTrace);
    }
  }

  Future<void> _handlePing(
    Map<String, dynamic> message,
  ) async {
    await send({
      'type': 'pong',
      'device_id': deviceId,
      'timestamp':
          DateTime.now().toUtc().toIso8601String(),
    });
  }

  Future<void> _handleHeartbeat(
    Map<String, dynamic> message,
  ) async {
    await send({
      'type': 'heartbeat',
      'device_id': deviceId,
      'timestamp':
          DateTime.now().toUtc().toIso8601String(),
    });
  }

  Future<void> _handleCommand(
    Map<String, dynamic> message,
  ) async {
    if (_messageHandler != null) {
      await _messageHandler!(message);
    }
  }

  Future<void> _handleEvent(
    Map<String, dynamic> message,
  ) async {
    if (_messageHandler != null) {
      await _messageHandler!(message);
    }
  }

  Future<void> _handleResult(
    Map<String, dynamic> message,
  ) async {
    if (_messageHandler != null) {
      await _messageHandler!(message);
    }
  }

  void _handleError(
    Object error,
    StackTrace stackTrace,
  ) {
    print(
      '[DeviceConnection] WebSocket error: $error',
    );

    _setState(DeviceConnectionState.disconnected);

    _scheduleReconnect();
  }

  void _handleDisconnected() {
    print(
      '[DeviceConnection] WebSocket closed',
    );

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
            'type': 'heartbeat',
            'device_id': deviceId,
            'timestamp':
                DateTime.now()
                    .toUtc()
                    .toIso8601String(),
          });
        } catch (e) {
          print(
            '[DeviceConnection] Heartbeat failed: $e',
          );
        }
      },
    );
  }

  void _scheduleReconnect() {
    if (_disposed ||
        _manualDisconnect ||
        _reconnectTimer != null) {
      return;
    }

    _setState(DeviceConnectionState.reconnecting);

    print(
      '[DeviceConnection] Reconnecting in 5 seconds...',
    );

    _reconnectTimer = Timer(
      const Duration(seconds: 5),
      () {
        _reconnectTimer = null;

        if (!_disposed && !_manualDisconnect) {
          connect();
        }
      },
    );
  }

  void _setState(
    DeviceConnectionState newState,
  ) {
    if (_state == newState) return;

    _state = newState;

    print(
      '[DeviceConnection] State: $_state',
    );
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

    _state = DeviceConnectionState.disconnected;
  }
}