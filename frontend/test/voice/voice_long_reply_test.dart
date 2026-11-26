import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/services/voice/voice_controller.dart';
import 'package:pai_mobile/services/voice/voice_events.dart';
import 'package:pai_mobile/services/voice/voice_socket.dart';
import 'package:pai_mobile/ui/voice/voice_screen.dart';

import '../ui/golden_harness.dart';
import 'fakes.dart';

/// The voice screen shows state only: never the user's words nor the reply text (however long). It must
/// also lay out on every screen size with the typing field open and with an error showing.

const _ready = SessionReady(
  voiceSessionId: 'v', chatSessionId: 'c', inputSampleRate: 16000, outputSampleRate: 24000,
  maxUtteranceSeconds: 30, idleTimeoutSeconds: 120, maxSessionSeconds: 3600,
  stt: 'fake', tts: 'fake', partialTranscripts: true, bargeIn: true,
);

final _longReply = List.generate(
  14,
  (i) => 'Here is point number ${i + 1}: the forecast for Berlin this afternoon is cloudy with a light drizzle.',
).join(' ');

Future<VoiceController> _open(WidgetTester t, {Size size = phone, double textScale = 1.0}) async {
  final c = VoiceController(
    socket: VoiceSocket(factory: () async => FakeTransport()),
    mic: FakeMic(),
    player: FakePlayer(),
    pingEvery: const Duration(hours: 1),
  );
  await pumpHud(t, VoiceScreen(controller: c, deviceId: 'dev'), size: size, textScale: textScale);
  c.handleEvent(_ready);
  c.connection = VoiceConnection.connected;
  return c;
}

Future<void> _settle(WidgetTester t) async {
  for (var i = 0; i < 6; i++) {
    await t.pump(const Duration(milliseconds: 60));
  }
}

Future<void> _stop(WidgetTester t, VoiceController c) =>
    t.runAsync(() => c.stop().timeout(const Duration(seconds: 2)));

void main() {
  setUpAll(loadPaiFonts);

  for (final (name, size, scale) in [
    ('phone', phone, 1.0),
    ('phone, large text', phone, 1.3),
    ('desktop window', desktop, 1.0),
    ('small phone', const Size(320, 568), 1.0),
  ]) {
    testWidgets('a long turn shows no text and does not overflow ($name)', (t) async {
      final c = await _open(t, size: size, textScale: scale);
      c.handleEvent(const TranscriptFinal('t', 'What is the weather in Berlin?'));
      c.handleEvent(AssistantTextDelta('t', 'r', _longReply));
      c.handleEvent(const StateChanged(VoiceState.speaking));
      await _settle(t);
      expect(t.takeException(), isNull);
      expect(find.textContaining('Berlin'), findsNothing);
      expect(find.text('SPEAKING'), findsNothing); // label is the plain "Speaking"
      expect(find.text('Speaking'), findsOneWidget);
      await _stop(t, c);
    });
  }

  testWidgets('with the typing field open it still lays out and shows no reply text', (t) async {
    final c = await _open(t, size: const Size(360, 640));
    c.handleEvent(AssistantTextDelta('t', 'r', _longReply));
    c.handleEvent(const StateChanged(VoiceState.speaking));
    await t.tap(find.byTooltip('Type instead'));
    await _settle(t);
    expect(t.takeException(), isNull);
    expect(find.textContaining('forecast'), findsNothing);
    await _stop(t, c);
  });

  testWidgets('an error is still shown', (t) async {
    final c = await _open(t);
    c.handleEvent(const VoiceError(code: 'tts_failed', message: 'could not speak part of the reply', fatal: false));
    await _settle(t);
    expect(find.text('could not speak part of the reply'), findsOneWidget);
    expect(t.takeException(), isNull);
    await _stop(t, c);
  });
}
