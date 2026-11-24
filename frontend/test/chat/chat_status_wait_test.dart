import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/chat/chat_status_service.dart';
import 'package:pai_mobile/core/api/api_client.dart';

ChatStatusService _svc({String sid = 's1'}) =>
    ChatStatusService(api: ApiClient(baseUrl: 'http://test', apiPrefix: '/api/v1'), sessionId: sid);

String _connected() => jsonEncode({'type': 'connected', 'session_id': 's1'});
String _status(String stage, String message, {String sid = 's1'}) =>
    jsonEncode({'session_id': sid, 'request_id': 'r', 'stage': stage, 'message': message});

void main() {
  test('waitUntilConnected resolves true as soon as the server confirms the subscription', () async {
    final s = _svc();
    final waiting = s.waitUntilConnected(timeout: const Duration(seconds: 2));
    s.debugHandleFrame(_connected());
    expect(await waiting, isTrue);
  });

  test('already confirmed: it returns true at once', () async {
    final s = _svc()..debugHandleFrame(_connected());
    expect(await s.waitUntilConnected(timeout: const Duration(milliseconds: 1)), isTrue);
  });

  test('never confirmed: it gives up after the timeout instead of blocking the caller', () async {
    final s = _svc();
    final t0 = DateTime.now();
    expect(await s.waitUntilConnected(timeout: const Duration(milliseconds: 80)), isFalse);
    expect(DateTime.now().difference(t0), lessThan(const Duration(seconds: 1)));
  });

  test('a disposed service never reports connected', () async {
    final s = _svc()..debugHandleFrame(_connected());
    s.dispose();
    expect(await s.waitUntilConnected(), isFalse);
  });

  test('status frames for its own session update the live status; other sessions are ignored', () {
    final s = _svc()..debugHandleFrame(_connected());
    s.debugHandleFrame(_status('planning', 'Understanding your request…'));
    expect(s.current?.message, 'Understanding your request…');
    expect(s.isWorking, isTrue);
    s.debugHandleFrame(_status('replying', 'Composing reply…', sid: 'someone-elses'));
    expect(s.current?.message, 'Understanding your request…');
    s.debugHandleFrame(_status('replying', 'Composing reply…'));
    expect(s.current?.message, 'Composing reply…');
  });
}
