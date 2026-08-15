import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../models/message.dart';
import '../services/notification_service.dart';
import 'device_service.dart';

typedef EventCallback = void Function(Map<String, dynamic> event);
typedef ConnectionErrorCallback = void Function(String error);

class WebSocketService {
  WebSocketChannel? _channel;
  Completer<void>? _connectionCompleter;  // not final, reassignable
  String? _connectionError;
  bool _isClosed = false;
  bool _isConnected = false;
  int _reconnectAttempts = 0;
  static const int maxReconnectAttempts = 5;
  Timer? _reconnectTimer;

  final Function(AIMessage) onMessage;
  final EventCallback? onEvent;
  final ConnectionErrorCallback? onConnectionError;

  WebSocketService({
    required this.onMessage,
    this.onEvent,
    this.onConnectionError,
  });

  String? get connectionError => _connectionError;
  bool get isConnected => _isConnected;

  Future<void> connect() async {
    if (_isConnected) return;
    _isClosed = false;
    _reconnectAttempts = 0;

    // Create a fresh completer for this connection attempt
    _connectionCompleter = Completer<void>();

    try {
      final wsUrl = (dotenv.env['WS_URL'] ?? 'ws://10.0.2.2:8000/ws').replaceAll('#', '');

      debugPrint('🔌 WebSocket: Attempting to connect to $wsUrl');

      _channel = WebSocketChannel.connect(Uri.parse(wsUrl));

      // Listen to the stream
      _channel!.stream.listen(
        (data) {
          // Mark connected
          if (!_isConnected) {
            _isConnected = true;
            _reconnectAttempts = 0;
            if (_connectionCompleter != null && !_connectionCompleter!.isCompleted) {
              _connectionCompleter!.complete();
            }
            debugPrint('✅ WebSocket connected (data received)');
          }
          // Process message
          try {
            final json = jsonDecode(data);
            if (json['type'] == 'notification.mobile') {
              NotificationService.showNotification(
                json['data']['title'],
                json['data']['message'],
              );
            } else if (json['type'] == 'event') {
              if (onEvent != null) onEvent!(json);
            } else {
              onMessage(AIMessage.fromJson(json));
            }
          } catch (e) {
            debugPrint('Message parse error: $e');
          }
        },
        onError: (error) {
          debugPrint('❌ WebSocket error: $error');
          _handleDisconnection(error.toString());
        },
        onDone: () {
          debugPrint('🔌 WebSocket disconnected');
          _handleDisconnection('Disconnected');
        },
      );

      // Fallback: if we don't get any data within 2 seconds, consider connected
      Future.delayed(const Duration(seconds: 2), () {
        if (_connectionCompleter != null && !_connectionCompleter!.isCompleted && !_isClosed) {
          _isConnected = true;
          _connectionCompleter!.complete();
          debugPrint('✅ WebSocket connected (timeout fallback)');
        }
      });

      // Wait for the connection to establish
      await _connectionCompleter!.future.timeout(
        const Duration(seconds: 5),
        onTimeout: () {
          throw Exception('WebSocket connection timeout');
        },
      );

      debugPrint('✅ WebSocket ready');

      // Auto-register device
      try {
        final userId = dotenv.env['USER_ID'];
        String deviceId = dotenv.env['DEVICE_ID'] ?? await DeviceService.getOrCreateDeviceId();
        final resolvedUserId = userId ?? await DeviceService.getOrCreateUserId(fallback: 'default');
        sendDeviceRegister(
          resolvedUserId,
          deviceId,
          deviceName: dotenv.env['DEVICE_NAME'] ?? 'Mobile Device',
          deviceType: dotenv.env['DEVICE_TYPE'] ?? 'mobile',
          platform: dotenv.env['PLATFORM'] ?? 'flutter',
        );
      } catch (e) {
        debugPrint('Device auto-register skipped: $e');
      }
    } catch (e) {
      debugPrint('❌ WebSocket connection error: $e');
      _connectionError = e.toString();
      if (_connectionCompleter != null && !_connectionCompleter!.isCompleted) {
        _connectionCompleter!.completeError(e);
      }
      _handleDisconnection(e.toString());
      rethrow;
    }
  }

  void _handleDisconnection(String reason) {
    _isConnected = false;
    if (_isClosed) return; // intentional disconnect
    _connectionError = reason;
    if (_connectionCompleter != null && !_connectionCompleter!.isCompleted) {
      _connectionCompleter!.completeError(reason);
    }
    // Notify UI
    if (onConnectionError != null) {
      onConnectionError!('WebSocket disconnected: $reason');
    }
    // Attempt reconnect
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_isClosed) return;
    if (_reconnectAttempts >= maxReconnectAttempts) {
      debugPrint('❌ Max reconnect attempts reached. Giving up.');
      if (onConnectionError != null) {
        onConnectionError!('Unable to reconnect after $maxReconnectAttempts attempts.');
      }
      return;
    }
    _reconnectAttempts++;
    final delay = Duration(seconds: _reconnectAttempts * 2); // exponential: 2,4,6,8,10 sec
    debugPrint('🔄 Reconnecting in ${delay.inSeconds}s (attempt $_reconnectAttempts)');
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(delay, () {
      if (!_isClosed) {
        connect().catchError((e) {
          debugPrint('Reconnection attempt $_reconnectAttempts failed: $e');
          // Continue schedule; will be called again on failure
        });
      }
    });
  }

  Future<void> ensureConnected() async {
    if (_isConnected) return;
    // Try to reconnect if not connected
    if (!_isClosed) {
      debugPrint('🔄 ensureConnected: Not connected, attempting reconnect...');
      _reconnectTimer?.cancel();
      // Force immediate reconnect attempt
      await connect().catchError((e) {
        debugPrint('Reconnect failed: $e');
        // Re-throw to let caller know
        throw Exception('WebSocket not connected: $e');
      });
    } else {
      throw Exception('WebSocket is closed.');
    }
  }

  void sendDeviceRegister(String userId, String deviceId,
      {String? deviceName,
      String? deviceType,
      String? platform,
      Map<String, dynamic>? capabilities,
      Map<String, dynamic>? config}) {
    final msg = {
      'type': 'register_device',
      'payload': {
        'user_id': userId,
        'device_id': deviceId,
        'device_name': deviceName,
        'device_type': deviceType,
        'platform': platform,
        'capabilities': capabilities ?? {},
        'config': config ?? {},
      }
    };
    _channel?.sink.add(jsonEncode(msg));
  }

  Future<void> sendGoal(String goal, {Map<String, dynamic>? context}) async {
    await ensureConnected();
    final message = AIMessage(type: 'goal', goal: goal, context: context);
    final json = jsonEncode(message.toJson());
    debugPrint('📤 WebSocket: Sending goal: $json');
    _channel?.sink.add(json);
  }

  Future<void> sendContextUpdate(Map<String, dynamic> context) async {
    await ensureConnected();
    final msg = {'type': 'context_update', 'context': context};
    _channel?.sink.add(jsonEncode(msg));
  }

  void disconnect() {
    _isClosed = true;
    _reconnectTimer?.cancel();
    _channel?.sink.close();
    _isConnected = false;
  }

  Future<void> subscribe(String event) async {
    await ensureConnected();
    final msg = {'type': 'subscribe', 'event': event};
    _channel?.sink.add(jsonEncode(msg));
  }
}