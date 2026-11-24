import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';

import '../../core/api/api_client.dart';
import 'permission_models.dart';

/// A readable failure from one of the permission/invoke endpoints.
class PermissionsException implements Exception {
  const PermissionsException(this.message, {this.statusCode});
  final String message;
  final int? statusCode;
  @override
  String toString() => message;
}

/// We could not get a permission decision at all (offline, server down, endpoint missing).
/// The caller decides what to do — the tester refuses unless the user is an admin.
class PreflightUnavailable implements Exception {
  const PreflightUnavailable(this.reason);
  final String reason;
  @override
  String toString() => reason;
}

/// Server calls behind confirmation scopes, standing grants and the plugin tester.
///
/// Every failure is logged with `debugPrint` (what, status, body) in addition to the request
/// log `ApiClient` already writes.
class PermissionsService {
  PermissionsService(
    this.api, {
    String? sessionId,
    DateTime Function()? clock,
    Future<void> Function(Duration)? delay,
    Duration Function(int attempt)? backoff,
  })  : sessionId = sessionId ?? _newSessionId(),
        _clock = clock ?? DateTime.now,
        _delay = delay ?? Future<void>.delayed,
        _backoff = backoff ?? _defaultBackoff;

  static const _tag = '[PermissionsService]';

  static final Expando<PermissionsService> _shared = Expando('PermissionsService');

  /// One service per [ApiClient] for the whole app run, so "Allow for this session" means the
  /// same session wherever it is chosen (plugin tester, chat, ...).
  static PermissionsService of(ApiClient api) => _shared[api] ??= PermissionsService(api);

  final ApiClient api;

  /// Identifies THIS app run. "Allow for this session" grants made from the tester are tied
  /// to it, so they end when the app restarts.
  final String sessionId;

  final DateTime Function() _clock;
  final Future<void> Function(Duration) _delay;
  final Duration Function(int attempt) _backoff;

  bool? _isAdmin;

  static String _newSessionId() {
    final r = Random.secure();
    final b = List<int>.generate(8, (_) => r.nextInt(256));
    return 'app-${b.map((x) => x.toRadixString(16).padLeft(2, '0')).join()}';
  }

  /// 0.5 s, 1 s, 1.5 s, 2 s, then 3 s — a single pending tester run, never a global loop.
  static Duration _defaultBackoff(int attempt) {
    const ms = [500, 1000, 1500, 2000, 3000];
    return Duration(milliseconds: ms[min(attempt, ms.length - 1)]);
  }

  // ------------------------------------------------------------------ admin

  /// Last known value without a network call (null = never fetched).
  bool? get cachedIsAdmin => _isAdmin;

  /// `GET /auth/me`. A failure returns the last known value, or false (the safe default).
  Future<bool> isAdmin({bool refresh = false}) async {
    if (_isAdmin != null && !refresh) return _isAdmin!;
    try {
      final me = await api.get('/auth/me');
      _isAdmin = me is Map && me['is_admin'] == true;
    } catch (e) {
      debugPrint('$_tag could not read /auth/me ($e); admin=${_isAdmin ?? false}');
    }
    return _isAdmin ?? false;
  }

  // ----------------------------------------------------------------- grants

  Future<List<ConsentGrant>> listConsents() async {
    try {
      final data = await api.get('/consents');
      final raw = data is Map ? data['consents'] : (data is List ? data : null);
      if (raw is! List) {
        throw const PermissionsException('Unexpected /consents response.');
      }
      return [for (final j in raw) ?ConsentGrant.tryParse(j)];
    } catch (e, st) {
      debugPrint('$_tag listConsents failed: $e');
      debugPrintStack(stackTrace: st, label: '$_tag listConsents', maxFrames: 6);
      rethrow;
    }
  }

  Future<void> revoke(String consentId) async {
    try {
      await api.delete('/consents/${Uri.encodeComponent(consentId)}');
    } catch (e, st) {
      debugPrint('$_tag revoke($consentId) failed: $e');
      debugPrintStack(stackTrace: st, label: '$_tag revoke', maxFrames: 6);
      rethrow;
    }
  }

  // ----------------------------------------------------- confirm / deny task

  /// `POST /tasks/{id}/confirm`. Throws [ConfirmationException] with the server's message on
  /// 409/422 so the dialog can show it inline and let the user adjust or deny.
  Future<void> confirmTask(String taskId, ConfirmationDecision decision) async {
    try {
      await api.post('/tasks/$taskId/confirm', decision.toBody());
    } on ApiException catch (e) {
      debugPrint('$_tag confirm($taskId) rejected: ${e.statusCode} ${e.body}');
      if (e.statusCode == 409 || e.statusCode == 422 || e.statusCode == 400 || e.statusCode == 403) {
        throw ConfirmationException.fromBody(e.statusCode, e.body);
      }
      rethrow;
    }
  }

  Future<void> denyTask(String taskId) async {
    try {
      await api.post('/tasks/$taskId/deny', {});
    } on ApiException catch (e) {
      debugPrint('$_tag deny($taskId) failed: ${e.statusCode} ${e.body}');
      // 409 = it was no longer awaiting confirmation (already handled): denying is moot.
      if (e.statusCode != 409) rethrow;
    }
  }

  // ------------------------------------------------------------------ invoke

  /// `POST /devices/{id}/invoke`. 200 = finished (or admin immediate), 202 = needs approval.
  Future<InvokeOutcome> invoke({
    required String deviceId,
    required String capability,
    required Map<String, dynamic> parameters,
    double timeoutSeconds = 60,
    double? waitSeconds,
  }) async {
    try {
      final data = await api.post('/devices/$deviceId/invoke', {
        'capability': capability,
        'parameters': parameters,
        'timeout_seconds': timeoutSeconds,
        'session_id': sessionId,
        'wait_seconds': ?waitSeconds,
      });
      return InvokeOutcome.fromJson(data, fallbackCapability: capability);
    } on ApiException catch (e) {
      debugPrint('$_tag invoke($deviceId, $capability) failed: ${e.statusCode} ${e.body}');
      throw PermissionsException(_detailMessage(e.body) ?? 'The server refused this request.',
          statusCode: e.statusCode);
    }
  }

  /// `POST /authorize/preflight` — what would happen if this ran natively on this device?
  /// Throws [PreflightUnavailable] when no decision could be obtained at all.
  Future<PreflightResult> preflight({
    required String deviceId,
    required String capability,
    required Map<String, dynamic> parameters,
  }) async {
    try {
      final data = await api.post('/authorize/preflight', {
        'capability': capability,
        'device_id': deviceId,
        'parameters': parameters,
        'session_id': sessionId,
      });
      return PreflightResult.fromJson(data);
    } on UnauthorizedException {
      rethrow; // signed out: the app's own flow handles it
    } on ApiException catch (e) {
      debugPrint('$_tag preflight failed: ${e.statusCode} ${e.body}');
      throw PreflightUnavailable(
          _detailMessage(e.body) ?? 'The server could not check permissions (HTTP ${e.statusCode}).');
    } catch (e) {
      debugPrint('$_tag preflight unreachable: ${e.runtimeType}: $e');
      throw const PreflightUnavailable('The server could not be reached.');
    }
  }

  // ------------------------------------------------------------ task result

  Future<TaskRunState> getTask(String taskId) async {
    final data = await api.get('/tasks/$taskId');
    return TaskRunState.fromJson(data);
  }

  /// Waits for ONE task to finish: GET /tasks/{id} with a short, growing delay, capped at
  /// [cap]. This exists only while a tester run is pending; there is no background loop.
  /// Throws [TimeoutException] when the cap is reached.
  Future<TaskRunState> waitForTask(String taskId, {Duration cap = const Duration(seconds: 60)}) async {
    final deadline = _clock().add(cap);
    var attempt = 0;
    while (true) {
      final state = await getTask(taskId);
      if (state.terminal) return state;
      if (!_clock().isBefore(deadline)) {
        throw TimeoutException('Still running after ${cap.inSeconds} s', cap);
      }
      await _delay(_backoff(attempt++));
    }
  }

  static String? _detailMessage(Object? body) {
    if (body is Map) {
      final d = body['detail'];
      if (d is String) return d;
      if (d is Map) return d['message']?.toString();
    }
    return null;
  }
}
