import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/chat/chat_service.dart';
import 'package:pai_mobile/chat/sse_client.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _RecordingSse extends SseClient {
  Map<String, dynamic>? lastBody;

  @override
  Stream<SseEvent> post(Uri url, {required Map<String, String> headers, required Object body}) {
    lastBody = Map<String, dynamic>.from(body as Map);
    return const Stream.empty();
  }
}

ChatService _service() => ChatService(ApiClient(baseUrl: 'http://test', apiPrefix: '/api/v1'));

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('a client session id is the same 32-char hex shape the server mints, and unique', () {
    final s = _service();
    final a = s.beginClientSession();
    final b = s.beginClientSession();
    expect(a, matches(RegExp(r'^[0-9a-f]{32}$')));
    expect(b, isNot(a));
    expect(s.sessionId, b);
  });

  test('it is held in memory only: a crash before the server confirms it leaves nothing behind', () async {
    final s = _service();
    s.beginClientSession();
    final relaunched = _service();
    await relaunched.restoreSession();
    expect(relaunched.sessionId, isNull);
  });

  test('the first message of a new chat is sent under that id (the server then creates the session with it)', () async {
    final s = _service();
    final id = s.beginClientSession();
    final sse = _RecordingSse();
    await s.sendMessageStream(deviceId: 'dev', message: 'hello', client: sse).toList();
    expect(sse.lastBody!['session_id'], id);
  });

  test('starting a new session forgets the id again (the next message mints a fresh one)', () async {
    final s = _service();
    final first = s.beginClientSession();
    await s.startNewSession();
    expect(s.sessionId, isNull);
    expect(s.beginClientSession(), isNot(first));
  });
}
