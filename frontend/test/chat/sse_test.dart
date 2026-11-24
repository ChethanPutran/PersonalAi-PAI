import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:pai_mobile/chat/chat_stream.dart';
import 'package:pai_mobile/chat/sse_client.dart';

List<SseEvent> feed(List<String> chunks) {
  final p = SseParser();
  return [for (final c in chunks) ...p.add(c)];
}

void main() {
  group('SseParser', () {
    test('basic events, event names, ids', () {
      final ev = feed(['event: delta\ndata: {"delta":"Hi"}\nid: 1\n\nevent: done\ndata: {}\n\n']);
      expect(ev.length, 2);
      expect(ev[0].event, 'delta');
      expect(ev[0].data, '{"delta":"Hi"}');
      expect(ev[0].id, '1');
      expect(ev[1].event, 'done');
    });

    test('default event name is "message"', () {
      expect(feed(['data: x\n\n']).single.event, 'message');
    });

    test('chunk boundaries anywhere (mid-line, mid-event, byte at a time)', () {
      const raw = 'event: delta\ndata: {"delta":"Hello"}\n\nevent: delta\ndata: {"delta":" there"}\n\n';
      final ev = feed(raw.split('')); // one character per chunk
      expect(ev.map((e) => e.data), ['{"delta":"Hello"}', '{"delta":" there"}']);
    });

    test('CRLF and a CR/LF pair split across chunks', () {
      expect(feed(['data: a\r\n\r\n']).single.data, 'a');
      final ev = feed(['data: a\r', '\ndata: b\r', '\n\r', '\n']);
      expect(ev.single.data, 'a\nb');
    });

    test('multi-line data, comments/heartbeats ignored, no leading-space strip beyond one', () {
      final ev = feed([': heartbeat\n\ndata: line1\ndata:  line2\n\n']);
      expect(ev.single.data, 'line1\n line2');
    });

    test('incomplete trailing event is not dispatched', () {
      expect(feed(['event: delta\ndata: x']), isEmpty);
    });

    test('parseSse decodes UTF-8 split inside a multi-byte character', () {
      final bytes = utf8.encode('event: delta\ndata: {"delta":"héllo 🙂"}\n\n');
      final cut = bytes.indexOf(0xC3) + 1; // between the two bytes of "é"
      final s = Stream<List<int>>.fromIterable([bytes.sublist(0, cut), bytes.sublist(cut)]);
      expectLater(parseSse(s).map((e) => e.data), emits('{"delta":"héllo 🙂"}'));
    });
  });

  group('ChatStreamEvent.fromSse', () {
    ChatStreamEvent? ev(String name, String data) =>
        ChatStreamEvent.fromSse(SseEvent(event: name, data: data));

    test('status / delta / done / error', () {
      expect(ev('status', '{"stage":"planning","message":"Thinking"}'), isA<ChatStreamStatus>());
      expect((ev('delta', '{"delta":"Hi"}') as ChatStreamDelta).delta, 'Hi');
      final done = ev('done', '{"message":"Hi there","status":"completed","session_id":"s1","tasks":[]}')
          as ChatStreamDone;
      expect(done.response.message, 'Hi there');
      expect(done.response.sessionId, 's1');
      expect((ev('error', '{"message":"boom"}') as ChatStreamError).message, 'boom');
    });

    test('unknown events and bad payloads are skipped', () {
      expect(ev('future.thing', '{}'), isNull);
      expect(ev('delta', 'not json'), isNull);
    });
  });

  group('SseClient', () {
    test('streams events and surfaces HTTP errors', () async {
      final ok = SseClient(clientFactory: () => MockClient.streaming((req, _) async {
            expect(req.headers['Accept'], 'text/event-stream');
            expect(req.headers['Idempotency-Key'], 'k1');
            final body = Stream<List<int>>.fromIterable([
              utf8.encode('event: delta\ndata: {"delta":"A"}\n\n'),
              utf8.encode('event: done\ndata: {"message":"A","status":"completed","session_id":"s"}\n\n'),
            ]);
            return http.StreamedResponse(body, 200);
          }));
      final events = await ok
          .post(Uri.parse('http://x/chat/stream'), headers: {'Idempotency-Key': 'k1'}, body: {'message': 'hi'})
          .toList();
      expect(events.map((e) => e.event), ['delta', 'done']);

      final bad = SseClient(clientFactory: () => MockClient.streaming((req, _) async {
            return http.StreamedResponse(Stream.value(utf8.encode('nope')), 503);
          }));
      await expectLater(
        bad.post(Uri.parse('http://x/chat/stream'), headers: {}, body: {}).toList(),
        throwsA(isA<SseHttpException>().having((e) => e.statusCode, 'status', 503)),
      );
    });
  });
}
