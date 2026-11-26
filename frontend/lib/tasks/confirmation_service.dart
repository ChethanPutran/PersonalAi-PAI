// lib/tasks/confirmation_service.dart

import '../core/redact.dart';
import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../core/api/api_client.dart';
import '../plugins/permissions/permission_models.dart';

class PendingConfirmation {
  final String taskId;
  final String capability;
  final Map<String, dynamic> parameters;
  final String reason;

  /// What the approval dialog shows: risk, parameter summary and — from the server — which
  /// scopes (once / session / always) and parameter bindings may be offered. Built from the
  /// event's `confirmation` block; older events without one get a "once only" request.
  final ConfirmationRequest request;

  PendingConfirmation({
    required this.taskId,
    required this.capability,
    required this.parameters,
    required this.reason,
    ConfirmationRequest? request,
  }) : request = request ??
            ConfirmationRequest.legacy(
              capability: capability,
              parameters: parameters,
              reason: reason,
            );
}

/// The WebSocket that carries `task.*` events (incl. `task.confirmation_requested`) for one chat session:
/// `WS /tasks/{session_id}/stream?token=<jwt>`. (`/tasks/stream` is NOT a WebSocket route — it only matches
/// `GET /tasks/{task_id}` — so a socket opened there is closed at once and no approval prompt would ever reach the user.)
@visibleForTesting
Uri confirmationSocketUri({
  required String wsBase,
  required String apiPrefix,
  required String sessionId,
  required String token,
}) =>
    Uri.parse('$wsBase$apiPrefix/tasks/${Uri.encodeComponent(sessionId)}/stream'
        '?token=${Uri.encodeComponent(token)}');

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
      
      final url = confirmationSocketUri(
          wsBase: cleanBase, apiPrefix: api.apiPrefix, sessionId: sessionId, token: token);

      debugPrint('[ConfirmationService] Connecting to WebSocket: ${redactUrl(url)}');
      _ws = WebSocketChannel.connect(url);

      _sub = _ws!.stream.listen(
        handleMessage,
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

  /// Parses one frame of the confirmation stream. Public so tests can feed it directly.
  @visibleForTesting
  void handleMessage(dynamic raw) {
    try {
      final data = raw is String ? jsonDecode(raw) : raw;
      if (data is Map && (data['type'] == 'task.confirmation_requested' || data['event'] == 'task.confirmation_requested')) {
        final capability = data['capability'] as String? ?? 'Unknown Capability';
        final parameters = Map<String, dynamic>.from(data['parameters'] ?? {});
        final reason = data['reason'] as String? ?? '';
        _pending = PendingConfirmation(
          taskId: (data['task_id'] ?? data['id']) as String,
          capability: capability,
          parameters: parameters,
          reason: reason,
          request: data['confirmation'] is Map
              ? ConfirmationRequest.fromJson(data['confirmation'], fallbackCapability: capability)
              : null,
        );
        notifyListeners();
      }
    } catch (e) {
      debugPrint('[ConfirmationService] Error parsing incoming WS message: $e');
    }
  }

  /// Approves the pending task with the user's chosen scope/binding (the shared approval
  /// dialog calls this). Unlike [confirm] it does NOT swallow server errors: a 409/422 is
  /// thrown as [ConfirmationException] so the dialog can show it and keep the task pending.
  Future<void> confirmWith(ConfirmationDecision decision) async {
    final taskId = _pending?.taskId;
    if (taskId == null) {
      throw const ConfirmationException('There is nothing waiting for approval.');
    }
    try {
      await api.post('/tasks/$taskId/confirm', decision.toBody());
    } on ApiException catch (e) {
      debugPrint('[ConfirmationService] confirm($taskId) rejected: ${e.statusCode} ${e.body}');
      if (e.statusCode == 409 || e.statusCode == 422 || e.statusCode == 400 || e.statusCode == 403) {
        throw ConfirmationException.fromBody(e.statusCode, e.body);
      }
      rethrow;
    }
    _pending = null;
    notifyListeners();
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