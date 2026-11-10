  import 'package:shared_preferences/shared_preferences.dart';

import '../core/api/api_client.dart';
import '../models/chat.dart';

class ChatService {
  final ApiClient api;
  String? _sessionId; 

  ChatService(this.api);

  String? get sessionId => _sessionId;

  Future<void> setSessionId(String? id) async {
    _sessionId = id;
    await _persistSession();
  }
  Future<void> restoreSession() async {
    final prefs = await SharedPreferences.getInstance();
    _sessionId = prefs.getString('chat_session_id');
  }

  Future<void> _persistSession() async {
    final prefs = await SharedPreferences.getInstance();
    if (_sessionId != null) {
      await prefs.setString('chat_session_id', _sessionId!);
    } else {
      await prefs.remove('chat_session_id');
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
    await setSessionId(null);
  }
}