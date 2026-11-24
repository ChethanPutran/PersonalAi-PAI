import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/services/voice/voice_controller.dart';
import 'package:pai_mobile/services/voice/voice_events.dart';
import 'package:pai_mobile/services/voice/voice_socket.dart';
import 'package:pai_mobile/ui/voice/voice_screen.dart';

import '../ui/golden_harness.dart';
import 'fakes.dart';

/// Regression: a long assistant reply made the voice screen report "bottom overflowed by N pixels".
/// HudPanel wraps its child in a shrink-wrapping Column, so the caption's scroll view got UNBOUNDED height,
/// sized itself to the text, and the panel overflowed by (text height - the space the screen gave it).

const _ready = SessionReady(
  voiceSessionId: 'v', chatSessionId: 'c', inputSampleRate: 16000, outputSampleRate: 24000,
  maxUtteranceSeconds: 30, idleTimeoutSeconds: 120, maxSessionSeconds: 3600,
  stt: 'fake', tts: 'fake', partialTranscripts: true, bargeIn: true,
);

final _longReply = List.generate(
  14,
  (i) => 'Here is point number ${i + 1}: the forecast for Berlin this afternoon is cloudy with a light drizzle, '
      'temperatures around ten degrees, and a gentle breeze from the west.',
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
    testWidgets('a very long reply does not overflow ($name)', (t) async {
      final c = await _open(t, size: size, textScale: scale);
      c.handleEvent(const TranscriptFinal('t', 'What is the weather in Berlin?'));
      c.handleEvent(AssistantTextDelta('t', 'r', _longReply));
      c.handleEvent(const StateChanged(VoiceState.speaking));
      await _settle(t);
      expect(t.takeException(), isNull);
      await _stop(t, c);
    });
  }

  testWidgets('a long reply with the typing field open does not overflow', (t) async {
    final c = await _open(t, size: const Size(360, 640));
    c.handleEvent(AssistantTextDelta('t', 'r', _longReply));
    c.handleEvent(const StateChanged(VoiceState.speaking));
    await t.tap(find.byTooltip('Type instead'));
    await _settle(t);
    expect(t.takeException(), isNull);
    await _stop(t, c);
  });

  testWidgets('a long reply stays reachable: the caption area scrolls to the newest text', (t) async {
    final c = await _open(t);
    c.handleEvent(AssistantTextDelta('t', 'r', _longReply));
    c.handleEvent(const StateChanged(VoiceState.speaking));
    await _settle(t);
    final scroll = t.state<ScrollableState>(find.descendant(of: find.byType(VoiceScreen), matching: find.byType(Scrollable)).first);
    expect(scroll.position.maxScrollExtent, greaterThan(0));            // it scrolls, not overflows
    expect(scroll.position.pixels, 0);                                  // reverse: true keeps the latest text in view
    await _stop(t, c);
  });

  testWidgets('a short reply still shrink-wraps (the panel is not stretched to fill the slot)', (t) async {
    final c = await _open(t);
    c.handleEvent(const AssistantTextDelta('t', 'r', 'Sunny.'));
    c.handleEvent(const StateChanged(VoiceState.speaking));
    await _settle(t);
    final panel = t.getSize(find.ancestor(of: find.text('Sunny.'), matching: find.byType(SingleChildScrollView)).first);
    expect(panel.height, lessThan(120));
    expect(t.takeException(), isNull);
    await _stop(t, c);
  });
}
