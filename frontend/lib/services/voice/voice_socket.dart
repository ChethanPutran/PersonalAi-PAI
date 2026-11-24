import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../core/api/api_client.dart';
import 'voice_events.dart';

/// Minimal duplex transport, so [VoiceSocket] can be tested without a network.
abstract class VoiceTransport {
  /// `String` (JSON text frame) or `Uint8List`/`List<int>` (binary frame).
  Stream<Object?> get incoming;
  void sendText(String data);
  void sendBinary(Uint8List data);
  int? get closeCode;
  Future<void> close();
}

typedef VoiceTransportFactory = Future<VoiceTransport> Function();

class WebSocketVoiceTransport implements VoiceTransport {
  final WebSocketChannel _ch;
  WebSocketVoiceTransport._(this._ch);

  static Future<WebSocketVoiceTransport> connect(Uri url) async {
    final ch = WebSocketChannel.connect(url);
    await ch.ready; // throws on a rejected upgrade -> handled by the caller
    return WebSocketVoiceTransport._(ch);
  }

  @override
  Stream<Object?> get incoming => _ch.stream.cast<Object?>();
  @override
  void sendText(String data) => _ch.sink.add(data);
  @override
  void sendBinary(Uint8List data) => _ch.sink.add(data);
  @override
  int? get closeCode => _ch.closeCode;
  @override
  Future<void> close() async {
    try {
      await _ch.sink.close();
    } catch (_) {}
  }
}

/// `ws(s)://<host>/api/v1/voice/stream?token=<jwt>` from the API client.
VoiceTransportFactory voiceTransportFor(ApiClient api) {
  return () async {
    final token = api.authToken;
    if (token == null || token.isEmpty) {
      throw StateError('Not signed in: no auth token for the voice stream');
    }
    final wsBase = api.baseUrl
        .replaceFirst('http://', 'ws://')
        .replaceFirst('https://', 'wss://');
    final url = Uri.parse(
      '$wsBase${api.apiPrefix}/voice/stream?token=${Uri.encodeComponent(token)}',
    );
    return WebSocketVoiceTransport.connect(url);
  };
}

enum VoiceConnection { idle, connecting, connected, reconnecting, closed }

/// One logical voice session over a (re)connecting WebSocket.
///
/// * Sends `session.start` first on every (re)connect, re-using the chat
///   session id the server reported so history/memory continue.
/// * Surfaces JSON frames as typed [events] and binary frames as [audio].
/// * Reconnects with exponential backoff after an unexpected drop, but never
///   after a user close, `session.ended`, an auth rejection (4401/4403) or a
///   fatal error event.
class VoiceSocket {
  final VoiceTransportFactory _factory;
  final SessionStartMsg _start;
  final Duration _initialBackoff;
  final Duration _maxBackoff;
  final int _maxAttempts;

  final _events = StreamController<VoiceServerEvent>.broadcast();
  final _audio = StreamController<Uint8List>.broadcast();
  final _conn = StreamController<VoiceConnection>.broadcast();

  VoiceTransport? _t;
  StreamSubscription<Object?>? _sub;
  Timer? _retry;
  Duration _backoff;
  int _attempts = 0;
  bool _closedByUser = false;
  bool _terminal = false; // server said stop / fatal / auth failure
  String? _chatSessionId;
  VoiceConnection _state = VoiceConnection.idle;

  VoiceSocket({
    required VoiceTransportFactory factory,
    SessionStartMsg start = const SessionStartMsg(),
    Duration initialBackoff = const Duration(seconds: 1),
    Duration maxBackoff = const Duration(seconds: 15),
    int maxAttempts = 6,
  })  : _factory = factory,
        _start = start,
        _initialBackoff = initialBackoff,
        _maxBackoff = maxBackoff,
        _maxAttempts = maxAttempts,
        _backoff = initialBackoff;

  Stream<VoiceServerEvent> get events => _events.stream;
  Stream<Uint8List> get audio => _audio.stream;
  Stream<VoiceConnection> get connection => _conn.stream;
  VoiceConnection get state => _state;
  String? get chatSessionId => _chatSessionId;

  void _setState(VoiceConnection s) {
    if (_state == s) return;
    _state = s;
    if (!_conn.isClosed) _conn.add(s);
  }

  Future<void> connect() async {
    _closedByUser = false;
    _terminal = false;
    await _open(first: true);
  }

  Future<void> _open({required bool first}) async {
    _setState(first ? VoiceConnection.connecting : VoiceConnection.reconnecting);
    try {
      final t = await _factory();
      if (_closedByUser) {
        await t.close();
        return;
      }
      _t = t;
      _sub = t.incoming.listen(
        _onFrame,
        onError: (Object e) => _onDropped(error: e),
        onDone: _onDropped,
        cancelOnError: true,
      );
      t.sendText((_chatSessionId == null
              ? _start
              : _start.copyWith(sessionId: _chatSessionId))
          .encode());
      _setState(VoiceConnection.connected);
      _attempts = 0;
      _backoff = _initialBackoff;
    } catch (e) {
      debugPrint('[VoiceSocket] connect failed: $e');
      if (first && _attempts == 0) {
        // Let the very first failure surface to the caller (bad token, no network).
        _setState(VoiceConnection.closed);
        rethrow;
      }
      _scheduleRetry();
    }
  }

  void _onFrame(Object? frame) {
    if (frame is String) {
      final ev = VoiceServerEvent.parse(frame);
      if (ev == null) {
        debugPrint('[VoiceSocket] malformed frame ignored');
        return;
      }
      if (ev is SessionReady && ev.chatSessionId.isNotEmpty) {
        _chatSessionId = ev.chatSessionId;
      }
      if (ev is SessionEnded || (ev is VoiceError && ev.fatal)) {
        _terminal = true;
      }
      _events.add(ev);
    } else if (frame is List<int>) {
      _audio.add(frame is Uint8List ? frame : Uint8List.fromList(frame));
    }
  }

  void _onDropped({Object? error}) {
    final code = _t?.closeCode;
    _sub?.cancel();
    _sub = null;
    _t = null;
    if (_closedByUser || _terminal) {
      _setState(VoiceConnection.closed);
      return;
    }
    if (code == 4401 || code == 4403) {
      _terminal = true;
      _setState(VoiceConnection.closed);
      if (!_events.isClosed) {
        _events.add(VoiceError(
          code: 'auth',
          message: 'Voice session rejected (code $code)',
          fatal: true,
        ));
      }
      return;
    }
    _scheduleRetry();
  }

  void _scheduleRetry() {
    if (_closedByUser || _terminal) return;
    if (++_attempts > _maxAttempts) {
      _terminal = true;
      _setState(VoiceConnection.closed);
      if (!_events.isClosed) {
        _events.add(const VoiceError(
          code: 'connection_lost',
          message: 'Lost connection to the assistant',
          fatal: true,
        ));
      }
      return;
    }
    _setState(VoiceConnection.reconnecting);
    _retry?.cancel();
    _retry = Timer(_backoff, () {
      _backoff = Duration(
        milliseconds:
            (_backoff.inMilliseconds * 2).clamp(0, _maxBackoff.inMilliseconds),
      );
      _open(first: false);
    });
  }

  /// Sends a control message. Silently dropped while disconnected (controls are
  /// only meaningful for the live connection).
  void send(VoiceClientMessage msg) => _t?.sendText(msg.encode());

  /// Sends one binary PCM16LE mono microphone frame.
  void sendAudio(Uint8List pcm) {
    if (_state != VoiceConnection.connected) return;
    _t?.sendBinary(pcm);
  }

  Future<void> close() async {
    _closedByUser = true;
    _retry?.cancel();
    final t = _t;
    if (t != null) {
      try {
        t.sendText(const SessionEndMsg().encode());
      } catch (_) {}
    }
    await _sub?.cancel();
    _sub = null;
    await t?.close();
    _t = null;
    _setState(VoiceConnection.closed);
    await _events.close();
    await _audio.close();
    await _conn.close();
  }
}
