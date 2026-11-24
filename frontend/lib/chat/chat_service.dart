import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

import '../core/api/api_client.dart';
import '../models/chat.dart';
import 'chat_stream.dart';
import 'sse_client.dart';

class ChatService {
  final ApiClient api;
  String? _sessionId; 

  ChatService(this.api);

  String? get sessionId => _sessionId;

  /// Per-user key. `auth.userId` is stable across restarts, so a
  /// returning user gets their previous session back, and switching
  /// users doesn't hand them someone else's session id.
  String get _sessionKey =>
      'chat_session_id_${api.auth?.userId ?? "anon"}';


  Future<void> setSessionId(String? id) async {
    _sessionId = id;
    await _persistSession();
  }
  Future<void> restoreSession() async {
    final prefs = await SharedPreferences.getInstance();
    _sessionId = prefs.getString(_sessionKey);
  }
  Future<void> clearSession() async {
  _sessionId = null;
  await _persistSession();
}

  Future<void> _persistSession() async {
    final prefs = await SharedPreferences.getInstance();
    if (_sessionId != null) {
      await prefs.setString(_sessionKey, _sessionId!);
    } else {
      await prefs.remove(_sessionKey);
    }
  }


  /// Streams the reply token-by-token (`POST /chat/stream`, SSE).
  ///
  /// The same [idempotencyKey] can be reused for the non-streaming fallback
  /// ([sendMessage]) so a retry never runs the goal twice. On `done` the session
  /// id the server assigned is adopted, exactly like [sendMessage].
  Stream<ChatStreamEvent> sendMessageStream({
    required String deviceId,
    required String message,
    String mode = 'text',
    String? idempotencyKey,
    SseClient? client,
  }) async* {
    final sse = client ?? SseClient();
    final events = sse.post(
      api.urlFor('/chat/stream'),
      headers: {
        ...api.authHeaders,
        'Idempotency-Key': ?idempotencyKey,
      },
      body: {
        'device_id': deviceId,
        'message': message,
        'mode': mode,
        'session_id': ?_sessionId,
      },
    );
    await for (final raw in events) {
      final ev = ChatStreamEvent.fromSse(raw);
      if (ev == null) continue;
      if (ev is ChatStreamDone && ev.response.sessionId.isNotEmpty) {
        await setSessionId(ev.response.sessionId);
      }
      yield ev;
    }
  }

  Future<ChatResponse> sendMessage({
    required String deviceId,
    required String message,
    String mode = 'text',
    String? idempotencyKey,
  }) async {
    final result = await api.post(
      '/chat',
      {
        'device_id': deviceId,
        'message': message,
        'mode': mode,
        if (_sessionId != null) 'session_id': _sessionId,   
      },
      headers: idempotencyKey == null
          ? null
          : {'Idempotency-Key': idempotencyKey},
    );

    // If this was /new, adopt the new session id.
    final sid = result['session_id']?.toString();
    if (sid != null && sid.isNotEmpty) await setSessionId(sid);
    return ChatResponse.fromJson(result);

  }
  Future<List<ChatSessionSummary>> listSessions() async {
    final list = await api.get('/chat/sessions') as List;
    return list.map((e) => ChatSessionSummary.fromJson(e)).toList();
  }

  Future<ChatSessionDetail> loadSession(String id) async {
    final json = await api.get('/chat/sessions/$id');
    return ChatSessionDetail.fromJson(json);
  }

  Future<void> renameSession(String id, String title) async {
    await api.patch('/chat/sessions/$id', {'title': title});
  }

  Future<void> deleteSession(String id) async {
    await api.delete('/chat/sessions/$id');
  }


  Future<void> startNewSession() async {
    // A new chat has no session yet; the id is picked when the first message is sent
    // ([beginClientSession]) and persisted once the server confirms it.
    _sessionId = null;
    await _persistSession();
  }

  /// Picks the id of a brand-new conversation NOW, so the live-status and confirmation sockets (keyed by
  /// session id) can be open BEFORE the first message is sent.
  ///
  /// Without this the first message of a new chat had no socket at all (the server only used to hand out the
  /// id in its reply), so it showed a bare "Working…" while every later message showed stage-by-stage status,
  /// and a confirmation prompt in that first message could not reach the screen. The backend accepts a stream
  /// for a not-yet-existing session and the first POST creates it under the caller with this id (a 32-char hex
  /// id, the same shape the server mints). Held in memory only: it is persisted once the server confirms it.
  String beginClientSession() {
    final id = const Uuid().v4().replaceAll('-', '');
    _sessionId = id;
    return id;
  }
}
