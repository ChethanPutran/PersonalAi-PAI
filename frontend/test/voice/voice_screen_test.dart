import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/services/voice/voice_controller.dart';
import 'package:pai_mobile/services/voice/voice_events.dart';
import 'package:pai_mobile/services/voice/voice_socket.dart';
import 'package:pai_mobile/ui/voice/voice_screen.dart';

import 'fakes.dart';

void main() {
  testWidgets('shows state only (no transcript, no reply text), end closes', (tester) async {
    final transport = FakeTransport();
    final c = VoiceController(
      socket: VoiceSocket(factory: () async => transport),
      mic: FakeMic(),
      player: FakePlayer(),
      pingEvery: const Duration(hours: 1),
    );
    await tester.pumpWidget(MaterialApp(
      home: Builder(
        builder: (ctx) => Scaffold(
          body: TextButton(
            onPressed: () => Navigator.of(ctx).push(MaterialPageRoute<void>(
              builder: (_) => VoiceScreen(controller: c, deviceId: 'dev'),
            )),
            child: const Text('open'),
          ),
        ),
      ),
    ));
    await tester.tap(find.text('open'));
    await tester.pump(const Duration(milliseconds: 50));
    await tester.pump(const Duration(milliseconds: 50));

    c.handleEvent(SessionReady(
      voiceSessionId: 'v',
      chatSessionId: 'c',
      inputSampleRate: 16000,
      outputSampleRate: 24000,
      maxUtteranceSeconds: 30,
      idleTimeoutSeconds: 120,
      maxSessionSeconds: 3600,
      stt: 'fake',
      tts: 'fake',
      partialTranscripts: true,
      bargeIn: true,
    ));
    c.connection = VoiceConnection.connected;
    c.handleEvent(const TranscriptPartial('t', 'what is the wea'));
    await tester.pump(const Duration(milliseconds: 50));
    expect(find.text('what is the wea'), findsNothing); // your words are not shown: just answer

    c.handleEvent(const StateChanged(VoiceState.thinking));
    await tester.pump(const Duration(milliseconds: 50));
    expect(find.text('Thinking…'), findsOneWidget);

    c.handleEvent(const AssistantTextDelta('t', 'r', 'It is '));
    c.handleEvent(const AssistantTextDelta('t', 'r', 'sunny.'));
    c.handleEvent(const StateChanged(VoiceState.speaking));
    await tester.pump(const Duration(milliseconds: 50));
    expect(find.text('It is sunny.'), findsNothing); // the reply is spoken, not displayed
    expect(find.text('Speaking'), findsOneWidget);
    // ...but the controller still has both for the chat history / other consumers
    expect(c.assistantText, 'It is sunny.');

    // stop() closes stream controllers, which FakeAsync can't drain; run it on real time.
    final stopped = tester.runAsync(() => c.stop().timeout(const Duration(seconds: 2)));
    await stopped;
    await tester.tap(find.byIcon(Icons.call_end));
    await tester.pump(const Duration(milliseconds: 100));
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.byType(VoiceScreen), findsNothing);
  });
}
