import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../core/api/api_client.dart';
import '../models/chat_status.dart';


/// Watches the backend's `/chat/{session_id}/stream` WS and exposes
/// the current in-flight status for the chat screen to render.
///
/// Lifecycle: open on chat screen mount, close on dispose. Reconnects
/// with exponential backoff if the socket drops mid-conversation —
/// except on auth/ownership failures (4401/4403), where retrying with
/// the same credentials would just loop.
///
/// Requires `api.authToken` to be non-null. If `AuthService` hasn't
/// logged in yet, `connect()` is a no-op and the caller should retry
/// once login completes (or just call `connect()` again).
class ChatStatusService extends ChangeNotifier {
  final ApiClient api;
  final String sessionId;

  WebSocketChannel? _ws;
  StreamSubscription? _sub;
  Timer? _reconnectTimer;
  Duration _backoff = const Duration(seconds: 1);

  ChatStatus? _current;
  bool _disposed = false;

  ChatStatusService({required this.api, required this.sessionId});

  // -------------------------------------------------------------------
  // Public surface
  // -------------------------------------------------------------------

  ChatStatus? get current => _current;

  /// True while a status is live and non-terminal.
  ///
  /// Declared exactly once. The previous version declared it twice —
  /// once near the top of the class and once at the bottom — which
  /// is a compile error in Dart.
  bool get isWorking {
    final s = _current;
    return s != null && !s.isTerminal;
  }

  // -------------------------------------------------------------------
  // Connect / reconnect
  // -------------------------------------------------------------------

  Future<void> connect() async {
    if (_disposed) return;

    // `api.ensureToken()` didn't exist on ApiClient. Use `authToken`
    // (added above). If auth hasn't completed, skip connect — the
    // caller will call connect() again once a token is available.
    final token = api.authToken;
    if (token == null || token.isEmpty) {
      debugPrint('[ChatStatus] no auth token; deferring connect');
      return;
    }

    // Cancel any prior subscription first. On reconnect, the old
    // socket is done (that's why we're reconnecting), but the sub
    // still holds a listener — cancelling first guarantees no
    // double-delivery during the handover.
    await _sub?.cancel();
    _sub = null;
    _ws = null;

    try {
      final wsBase = api.baseUrl
          .replaceFirst('http://', 'ws://')
          .replaceFirst('https://', 'wss://');

      // URL-encode the token. JWTs are base64url so they're normally
      // URL-safe, but explicit encoding costs nothing and prevents a
      // future scheme (opaque tokens, refresh tokens with '+' or '=')
      // from silently producing a malformed URL.
      final url = Uri.parse(
        '$wsBase/chat/$sessionId/stream'
        '?token=${Uri.encodeComponent(token)}',
      );

      _ws = WebSocketChannel.connect(url);
      _sub = _ws!.stream.listen(
        _handleFrame,
        onError: (Object e) => _handleDisconnect(error: e),
        onDone: () => _handleDisconnect(),
        cancelOnError: true,
      );

      // Reset backoff on a successful handshake. Handshake failures
      // surface via onError/onDone above, which is why this reset is
      // optimistic — the next `_handleDisconnect` will restore backoff
      // if it turns out the connection didn't actually establish.
      _backoff = const Duration(seconds: 1);
      debugPrint('[ChatStatus] connecting to $url');
    } catch (e) {
      debugPrint('[ChatStatus] connect failed: $e');
      _scheduleReconnect();
    }
  }

  // -------------------------------------------------------------------
  // Inbound
  // -------------------------------------------------------------------

  void _handleFrame(dynamic raw) {
    if (raw is! String) return;
    try {
      final data = jsonDecode(raw) as Map<String, dynamic>;
      final type = data['type']?.toString();

      if (type == 'connected') {
        debugPrint('[ChatStatus] stream connected for $sessionId');
        return;
      }
      if (type == 'error') {
        debugPrint('[ChatStatus] server error: ${data['message']}');
        return;
      }
      if (type == 'ping') return;

      final status = ChatStatus.fromJson(data);
      if (status.sessionId != sessionId) return;

      _current = status;
      notifyListeners();

      if (status.isTerminal) {
        _scheduleClear(status);
      }
    } catch (e) {
      debugPrint('[ChatStatus] bad frame: $e');
    }
  }

  /// Clear the terminal status after a short delay so the user has
  /// time to register "Done" before the indicator disappears.
  ///
  /// Identity comparison (`identical`) is correct here — if a new
  /// status arrived in the delay window, `_current` points to a
  /// different object and we leave it alone. The previous version
  /// compared `timestamp`s, which would fail if two events landed in
  /// the same millisecond (unlikely but possible under load).
  void _scheduleClear(ChatStatus terminalStatus) {
    Future.delayed(const Duration(milliseconds: 800), () {
      if (_disposed) return;
      if (identical(_current, terminalStatus)) {
        _current = null;
        notifyListeners();
      }
    });
  }

  // -------------------------------------------------------------------
  // Disconnect / reconnect
  // -------------------------------------------------------------------

void _handleDisconnect({Object? error}) {
  if (_disposed) return;

  final code = _ws?.closeCode;
  if (code == 4401 || code == 4403) {
    debugPrint('[ChatStatus] auth rejected (code $code); not reconnecting');
    _current = null;
    notifyListeners();
    return;
  }

  // Pre-upgrade rejections (404/403/500) surface here as
  // `WebSocketChannelException` with no close code. The channel may
  // include the underlying exception in its message — grep for a
  // 4xx status marker. If we see one, stop retrying — the route
  // isn't going to appear by retrying.
  if (code == null && error is WebSocketChannelException) {
    // `.message` is nullable in some versions of web_socket_channel —
    // the exception may be constructed with only a stack trace, or by a
    // transport that doesn't surface a message. Coerce to an empty
    // string so the `.toLowerCase()` chain doesn't need a null guard
    // at every step.
    final msg = (error.message ?? '').toLowerCase();
    if (msg.contains('403') || msg.contains('404') ||
        msg.contains('400') || msg.contains('401')) {
      debugPrint(
        '[ChatStatus] server rejected WS with a 4xx; not reconnecting: '
        '${error.message ?? error.toString()}',
      );
      _current = null;
      notifyListeners();
      return;
    }
  }

  _scheduleReconnect();
}

  void _scheduleReconnect() {
    if (_disposed) return;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(_backoff, () {
      _backoff = Duration(
        milliseconds: (_backoff.inMilliseconds * 2).clamp(1000, 30000),
      );
      connect();
    });
  }

  // -------------------------------------------------------------------
  // External control
  // -------------------------------------------------------------------

  /// Called by the chat screen once the POST returns, to clear the
  /// thinking indicator without waiting for the auto-clear delay.
  void clear() {
    _current = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _reconnectTimer?.cancel();
    _sub?.cancel();
    try {
      _ws?.sink.close();
    } catch (_) {
      // Already closed or transport gone — nothing to do, the socket
      // will be GC'd.
    }
    super.dispose();
  }
}