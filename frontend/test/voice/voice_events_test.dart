import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/services/voice/voice_events.dart';

import 'fakes.dart';

VoiceServerEvent parse(Map<String, Object?> m) => VoiceServerEvent.parse(jsonEncode(m))!;

void main() {
  group('server events', () {
    test('session.ready carries rates, limits and capabilities', () {
      final e = parse(readyEvent()) as SessionReady;
      expect(e.chatSessionId, 'chat-1');
      expect(e.inputSampleRate, 16000);
      expect(e.outputSampleRate, 24000);
      expect(e.maxUtteranceSeconds, 30.0);
      expect(e.partialTranscripts, isTrue);
      expect(e.bargeIn, isTrue);
    });

    test('state, speech and transcripts', () {
      expect((parse({'type': 'state', 'state': 'speaking', 'turn_id': 't1'}) as StateChanged).state,
          VoiceState.speaking);
      expect((parse({'type': 'state', 'state': 'bogus'}) as StateChanged).state, VoiceState.idle);
      expect((parse({'type': 'speech.started', 'turn_id': 't1'}) as SpeechStarted).turnId, 't1');
      final p = parse({'type': 'transcript.partial', 'turn_id': 't1', 'text': 'hel'}) as TranscriptPartial;
      expect(p.text, 'hel');
      final f = parse({'type': 'transcript.final', 'turn_id': 't1', 'text': 'hello'}) as TranscriptFinal;
      expect(f.text, 'hello');
    });

    test('assistant text, audio and interruption', () {
      final d = parse({'type': 'assistant.text.delta', 'turn_id': 't', 'response_id': 'r', 'delta': 'Hi'})
          as AssistantTextDelta;
      expect(d.delta, 'Hi');
      final a = parse({'type': 'assistant.audio.start', 'response_id': 'r', 'sample_rate': 24000})
          as AssistantAudioStart;
      expect(a.sampleRate, 24000);
      final i = parse({
        'type': 'assistant.interrupted',
        'response_id': 'r',
        'reason': 'barge_in',
        'spoken_text': 'Hi th',
      }) as AssistantInterrupted;
      expect(i.reason, InterruptReason.bargeIn);
      expect(i.spokenText, 'Hi th');
      expect(
        (parse({'type': 'assistant.interrupted', 'reason': 'cancel'}) as AssistantInterrupted).reason,
        InterruptReason.cancel,
      );
    });

    test('error, pong, ended', () {
      final e = parse({'type': 'error', 'code': 'x', 'message': 'boom', 'fatal': true}) as VoiceError;
      expect(e.fatal, isTrue);
      expect((parse({'type': 'pong', 'id': '7'}) as Pong).id, '7');
      expect((parse({'type': 'session.ended', 'reason': 'idle'}) as SessionEnded).reason, 'idle');
    });

    test('unknown types are tolerated; garbage is rejected', () {
      expect(parse({'type': 'brand.new'}), isA<UnknownVoiceEvent>());
      expect(VoiceServerEvent.parse('not json'), isNull);
      expect(VoiceServerEvent.parse('[1,2]'), isNull);
      expect(VoiceServerEvent.parse('{"no":"type"}'), isNull);
    });
  });

  group('client messages', () {
    test('session.start omits unset fields and nests client info', () {
      final j = const SessionStartMsg(sessionId: 's', deviceId: 'd', inputSampleRate: 16000).toJson();
      expect(j['type'], 'session.start');
      expect(j['session_id'], 's');
      expect(j.containsKey('language'), isFalse);
      expect((j['client'] as Map)['name'], 'pai_mobile');
    });

    test('control messages', () {
      expect(const ResponseCancelMsg().toJson(), {'type': 'response.cancel'});
      expect(const InputTextMsg('hey').toJson(), {'type': 'input.text', 'text': 'hey'});
      expect(const SessionEndMsg().toJson(), {'type': 'session.end'});
      expect(const PingMsg('1').toJson(), {'type': 'ping', 'id': '1'});
      expect(jsonDecode(const SessionUpdateMsg(voice: 'nova').encode()), {'type': 'session.update', 'voice': 'nova'});
    });
  });
}
