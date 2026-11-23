// lib/tasks/confirmation_service.dart

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../core/api/api_client.dart';

class PendingConfirmation {
  final String taskId;
  final String capability;
  final Map<String, dynamic> parameters;
  final String reason;

  PendingConfirmation({
    required this.taskId,
    required this.capability,
    required this.parameters,
    required this.reason,
  });
}

class ConfirmationService extends ChangeNotifier {
  final ApiClient api;
  final String sessionId;

  WebSocketChannel? _ws;
  StreamSubscription? _sub;

  PendingConfirmation? _pending;
  PendingConfirmation? get pending => _pending;

  ConfirmationService({
    required this.api, 
    required this.sessionId
  });

  /// Helper to convert http/https base URL to ws/wss WebSocket URL
  String _wsBase(String baseUrl) {
    final uri = Uri.parse(baseUrl);
    final isSecure = uri.scheme == 'https';
    final scheme = isSecure ? 'wss' : 'ws';
    return uri.replace(scheme: scheme).toString();
  }

  Future<void> connect() async {
    // Clean up any existing connection first
    await disposeConnection();

    final token = api.authToken;
    if (token == null) {
      debugPrint('[ConfirmationService] Cannot connect: auth token is null');
      return;
    }

    try {
      final base = _wsBase(api.baseUrl);
      // Remove trailing slash if present to avoid double slashes
      final cleanBase = base.endsWith('/') ? base.substring(0, base.length - 1) : base;
      
      final url = Uri.parse(
        '$cleanBase/api/v1/tasks/stream?session_id=$sessionId&token=${Uri.encodeComponent(token)}',
      );

      debugPrint('[ConfirmationService] Connecting to WebSocket: $url');
      _ws = WebSocketChannel.connect(url);

      _sub = _ws!.stream.listen(
        (raw) {
          try {
            final data = jsonDecode(raw as String);
            if (data is Map && data['type'] == 'task.confirmation_requested') {
              _pending = PendingConfirmation(
                taskId: data['task_id'] as String,
                capability: data['capability'] as String? ?? 'Unknown Capability',
                parameters: Map<String, dynamic>.from(data['parameters'] ?? {}),
                reason: data['reason'] as String? ?? '',
              );
              notifyListeners();
            }
          } catch (e) {
            debugPrint('[ConfirmationService] Error parsing incoming WS message: $e');
          }
        },
        onError: (error) {
          debugPrint('[ConfirmationService] WebSocket error: $error');
        },
        onDone: () {
          debugPrint('[ConfirmationService] WebSocket connection closed.');
        },
      );
       } catch (e) {
      debugPrint('[ConfirmationService] Failed to connect WebSocket: $e');
    }
  
  }

  Future<void> confirm() async {
    if (_pending == null) return;
    final taskId = _pending!.taskId;
    try {
      await api.post('/tasks/$taskId/confirm', {});
    } catch (e) {
      debugPrint('[ConfirmationService] Failed to confirm task $taskId: $e');
    } finally {
      _pending = null;
      notifyListeners();
    }
  }

  Future<void> deny() async {
    if (_pending == null) return;
    final taskId = _pending!.taskId;
    try {
      await api.post('/tasks/$taskId/deny', {});
    } catch (e) {
      debugPrint('[ConfirmationService] Failed to deny task $taskId: $e');
    } finally {
      _pending = null;
      notifyListeners();
    }
  }

  Future<void> disposeConnection() async {
    await _sub?.cancel();
    _sub = null;
    await _ws?.sink.close();
    _ws = null;
  }

  @override
  void dispose() {
    unawaited(disposeConnection());
    super.dispose();
  }
}