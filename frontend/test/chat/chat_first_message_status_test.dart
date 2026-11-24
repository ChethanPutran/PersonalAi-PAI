import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/chat/chat_status_service.dart';
import 'package:pai_mobile/chat/chat_stream.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/ui/chat/chat_screen.dart';

import '../ui/fakes.dart';
import '../ui/golden_harness.dart';

/// (The status line renders its text in capitals.)
///
/// Regression: the FIRST message of a new chat showed only "Working…" while later messages showed
/// "Understanding…", "Composing reply…". A new chat had no session id until the server's reply, so no
/// status socket existed for that first message.

class _Service extends FakeChatService {
  _Service(this.log);
  final List<String> log;

  @override
  String beginClientSession() {
    final id = super.beginClientSession();
    log.add('begin:$id');
    return id;
  }

  @override
  Stream<ChatStreamEvent> sendMessageStream({
    required String deviceId,
    required String message,
    String mode = 'text',
    String? idempotencyKey,
    dynamic client,
  }) {
    log.add('post');
    return super.sendMessageStream(
        deviceId: deviceId, message: message, mode: mode, idempotencyKey: idempotencyKey, client: client);
  }
}

class _Status extends ChatStatusService {
  _Status(ApiClient api, String sid, this.log) : super(api: api, sessionId: sid);
  final List<String> log;

  @override
  Future<void> connect() async => log.add('connect');

  @override
  Future<bool> waitUntilConnected({Duration timeout = const Duration(seconds: 2)}) async {
    log.add('waited');
    return true;
  }
}

String _frame(String sid, String stage, String message) =>
    jsonEncode({'session_id': sid, 'request_id': 'r', 'stage': stage, 'message': message});

void main() {
  setUpAll(loadPaiFonts);

  testWidgets('the first message of a new chat gets live status: socket opened and confirmed BEFORE the post', (t) async {
    final log = <String>[];
    late _Status status;
    String? sessionOfStatus;
    await pumpHud(
      t,
      ChatScreen(
        service: _Service(log),
        deviceId: 'dev',
        statusServiceFactory: (api, sid) {
          sessionOfStatus = sid;
          log.add('status:$sid');
          return status = _Status(api, sid, log);
        },
      ),
    );
    await t.pump(const Duration(milliseconds: 100));

    await t.enterText(find.byType(TextField), 'turn on the lights');
    await t.tap(find.byTooltip('Send'));
    await t.pump(const Duration(milliseconds: 100));

    final id = log.first.substring('begin:'.length);
    expect(id, matches(RegExp(r'^[0-9a-f]{32}$')));
    expect(sessionOfStatus, id);                                     // the socket is for the id the POST will use
    expect(log, ['begin:$id', 'status:$id', 'connect', 'waited', 'post']);

    // Nothing from the server yet: say "Working…" rather than showing a blank gap.
    expect(find.text('WORKING…'), findsOneWidget);

    // Then the real stages arrive, exactly as for any later message.
    status.debugHandleFrame(_frame(id, 'planning', 'Understanding your request…'));
    await t.pump(const Duration(milliseconds: 50));
    expect(find.text('UNDERSTANDING YOUR REQUEST…'), findsOneWidget);
    expect(find.text('WORKING…'), findsNothing);

    status.debugHandleFrame(_frame(id, 'replying', 'Composing reply…'));
    await t.pump(const Duration(milliseconds: 50));
    expect(find.text('COMPOSING REPLY…'), findsOneWidget);
    expect(find.text('UNDERSTANDING YOUR REQUEST…'), findsNothing);

    await t.pumpWidget(const SizedBox());                            // dispose the screen
  });
}
