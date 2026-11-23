  import 'package:shared_preferences/shared_preferences.dart';

import '../core/api/api_client.dart';
import '../models/chat.dart';

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


  Future<ChatResponse> sendMessage({
    required String deviceId,
    required String message,
    String mode = 'text',
  }) async {
    final result = await api.post(
      '/chat',
      {
        'device_id': deviceId,
        'message': message,
        'mode': mode,
        if (_sessionId != null) 'session_id': _sessionId,   
      },
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
    // Do NOT mint a local id. The server assigns the real session id
    // on the first POST /chat; until then there is no session to
    // subscribe to, and opening a WS on a fake id gets a 403.
    _sessionId = null;
    await _persistSession();
  }
}
