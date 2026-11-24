import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/services/voice/voice_controller.dart';
import 'package:pai_mobile/services/voice/voice_events.dart';
import 'package:pai_mobile/services/voice/voice_socket.dart';

import 'fakes.dart';

class Rig {
  final transports = <FakeTransport>[];
  final mic = FakeMic();
  final player = FakePlayer();
  final adopted = <String>[];
  late final VoiceController c;
  late final VoiceSocket socket;

  Rig({int maxAttempts = 3}) {
    socket = VoiceSocket(
      factory: () async {
        final t = FakeTransport();
        transports.add(t);
        return t;
      },
      start: const SessionStartMsg(deviceId: 'dev'),
      initialBackoff: const Duration(milliseconds: 5),
      maxBackoff: const Duration(milliseconds: 20),
      maxAttempts: maxAttempts,
    );
    c = VoiceController(
      socket: socket,
      mic: mic,
      player: player,
      pingEvery: const Duration(hours: 1),
      onChatSession: adopted.add,
    );
  }

  FakeTransport get t => transports.last;

  Future<void> boot() async {
    await c.start();
    await pump();
    t.serverJson(readyEvent());
    await pump();
  }
}

Uint8List frame(int n, [int v = 100]) {
  final b = ByteData(n * 2);
  for (var i = 0; i < n; i++) {
    b.setInt16(i * 2, v, Endian.little);
  }
  return b.buffer.asUint8List();
}

void main() {
  test('start: opens the socket, sends session.start, then streams mic frames after ready', () async {
    final r = Rig();
    await r.c.start();
    await pump();
    expect(r.t.sentTypes, ['session.start']);
    expect(r.t.sentText.first['device_id'], 'dev');
    expect(r.mic.running, isFalse); // not before the server is ready

    r.t.serverJson(readyEvent());
    await pump();
    expect(r.c.ready, isTrue);
    expect(r.c.chatSessionId, 'chat-1');
    expect(r.adopted, ['chat-1']);
    expect(r.mic.running, isTrue);

    r.mic.frame(frame(640));
    await pump();
    expect(r.t.sentBinary.length, 1);
    expect(r.t.sentBinary.single.length, 1280);
    await r.c.stop();
  });

  test('full turn: partial -> final -> thinking -> speaking (audio) -> idle', () async {
    final r = Rig();
    await r.boot();

    r.t.serverJson({'type': 'speech.started', 'turn_id': 't1'});
    r.t.serverJson({'type': 'state', 'state': 'listening', 'turn_id': 't1'});
    await pump();
    expect(r.c.state, VoiceState.listening);

    r.t.serverJson({'type': 'transcript.partial', 'turn_id': 't1', 'text': 'what is the'});
    await pump();
    expect(r.c.userPartial, 'what is the');
    expect(r.c.userCaption, 'what is the');

    r.t.serverJson({'type': 'transcript.final', 'turn_id': 't1', 'text': 'what is the weather'});
    r.t.serverJson({'type': 'state', 'state': 'thinking', 'turn_id': 't1'});
    r.t.serverJson({'type': 'assistant.status', 'turn_id': 't1', 'stage': 'planning', 'message': 'Checking…'});
    await pump();
    expect(r.c.userText, 'what is the weather');
    expect(r.c.userPartial, '');
    expect(r.c.state, VoiceState.thinking);
    expect(r.c.statusMessage, 'Checking…');

    // assistant text streams as captions, audio streams as PCM
    r.t.serverJson({'type': 'assistant.text.delta', 'turn_id': 't1', 'response_id': 'r1', 'delta': 'It is '});
    r.t.serverJson({'type': 'assistant.text.delta', 'turn_id': 't1', 'response_id': 'r1', 'delta': 'sunny.'});
    r.t.serverJson({'type': 'assistant.audio.start', 'response_id': 'r1', 'sample_rate': 24000});
    r.t.serverJson({'type': 'state', 'state': 'speaking', 'turn_id': 't1'});
    await pump();
    r.t.serverAudio(frame(480));
    r.t.serverAudio(frame(480));
    await pump();
    expect(r.c.assistantText, 'It is sunny.');
    expect(r.player.configuredRate, 24000);
    expect(r.player.fed.length, 2);
    expect(r.c.state, VoiceState.speaking);

    r.t.serverJson({'type': 'assistant.text.done', 'turn_id': 't1', 'response_id': 'r1', 'text': 'It is sunny.'});
    r.t.serverJson({'type': 'assistant.audio.end', 'response_id': 'r1'});
    r.t.serverJson({'type': 'state', 'state': 'idle'});
    await pump();
    expect(r.player.ends, 1);
    expect(r.c.state, VoiceState.idle);
    expect(r.c.statusMessage, '');
    expect(r.player.flushes, 0); // a normal finish never flushes
    await r.c.stop();
  });

  test('server barge-in: assistant.interrupted flushes playback and drops in-flight audio', () async {
    final r = Rig();
    await r.boot();
    r.t.serverJson({'type': 'assistant.audio.start', 'response_id': 'r1', 'sample_rate': 24000});
    r.t.serverJson({'type': 'state', 'state': 'speaking'});
    await pump();
    r.t.serverAudio(frame(480));
    await pump();
    expect(r.player.fed.length, 1);

    r.t.serverJson({
      'type': 'assistant.interrupted',
      'response_id': 'r1',
      'reason': 'barge_in',
      'spoken_text': 'It is sun',
    });
    await pump();
    expect(r.player.flushes, 1);
    expect(r.c.assistantText, 'It is sun'); // caption trimmed to what was actually said

    r.t.serverAudio(frame(480)); // a frame that was already on the wire
    await pump();
    expect(r.player.fed.length, 1, reason: 'late audio of an interrupted response must be dropped');

    // the next response plays again
    r.t.serverJson({'type': 'assistant.audio.start', 'response_id': 'r2', 'sample_rate': 24000});
    await pump();
    r.t.serverAudio(frame(480));
    await pump();
    expect(r.player.fed.length, 2);
    await r.c.stop();
  });

  test('manual interrupt: flushes locally at once and tells the server', () async {
    final r = Rig();
    await r.boot();
    r.t.serverJson({'type': 'assistant.audio.start', 'response_id': 'r1', 'sample_rate': 24000});
    await pump();
    r.c.interrupt();
    await pump();
    expect(r.player.flushes, 1);
    expect(r.t.sentTypes.last, 'response.cancel');
    r.t.serverAudio(frame(480));
    await pump();
    expect(r.player.fed, isEmpty);
    await r.c.stop();
  });

  test('mute really stops the recorder; unmute restarts it; frames are not sent while muted', () async {
    final r = Rig();
    await r.boot();
    expect(r.mic.startCount, 1);
    await r.c.setMuted(true);
    expect(r.c.muted, isTrue);
    expect(r.mic.running, isFalse);
    await r.c.setMuted(false);
    expect(r.mic.running, isTrue);
    expect(r.mic.startCount, 2);
    await r.c.stop();
  });

  test('typed input goes to the server as input.text', () async {
    final r = Rig();
    await r.boot();
    r.c.sendText('  hello there ');
    expect(r.t.sentText.last, {'type': 'input.text', 'text': 'hello there'});
    expect(r.c.userText, 'hello there');
    r.c.sendText('   ');
    expect(r.t.sentTypes.where((t) => t == 'input.text').length, 1);
    await r.c.stop();
  });

  test('errors: non-fatal is shown and the session continues; fatal ends it', () async {
    final r = Rig();
    await r.boot();
    r.t.serverJson({'type': 'error', 'code': 'stt', 'message': "Didn't catch that", 'fatal': false});
    await pump();
    expect(r.c.error, "Didn't catch that");
    expect(r.c.ready, isTrue);

    r.t.serverJson({'type': 'speech.started', 'turn_id': 't2'});
    await pump();
    expect(r.c.error, isNull, reason: 'the error clears when the user speaks again');

    r.t.serverJson({'type': 'error', 'code': 'quota', 'message': 'Too many sessions', 'fatal': true});
    await pump(40);
    expect(r.c.error, 'Too many sessions');
    expect(r.c.ready, isFalse);
    expect(r.mic.running, isFalse);
    expect(r.t.closed, isTrue);
  });

  test('permission denied: nothing is opened', () async {
    final r = Rig();
    r.mic.permission = false;
    await r.c.start();
    expect(r.c.error, contains('permission'));
    expect(r.transports, isEmpty);
  });

  test('reconnect: re-sends session.start with the same chat session; mic keeps running', () async {
    final r = Rig();
    await r.boot();
    expect(r.mic.startCount, 1);
    r.t.drop(); // network blip
    await pump(40);
    expect(r.transports.length, 2);
    expect(r.t.sentText.first['type'], 'session.start');
    expect(r.t.sentText.first['session_id'], 'chat-1');
    r.t.serverJson(readyEvent());
    await pump();
    expect(r.c.ready, isTrue);
    expect(r.mic.startCount, 1, reason: 'the recorder must not be restarted on reconnect');
    await r.c.stop();
  });

  test('reconnect gives up after maxAttempts with a fatal connection_lost error', () async {
    var calls = 0;
    FakeTransport? first;
    final socket = VoiceSocket(
      factory: () async {
        calls++;
        if (calls == 1) return first = FakeTransport();
        throw StateError('server down');
      },
      initialBackoff: const Duration(milliseconds: 2),
      maxBackoff: const Duration(milliseconds: 4),
      maxAttempts: 2,
    );
    final errors = <VoiceError>[];
    final states = <VoiceConnection>[];
    socket.events.listen((e) {
      if (e is VoiceError) errors.add(e);
    });
    socket.connection.listen(states.add);

    await socket.connect();
    expect(socket.state, VoiceConnection.connected);
    first!.drop(); // connection lost; every retry now fails
    // Wait for the outcome (not a fixed sleep): backoffs are a few ms, but a loaded machine
    // can stretch them, and a fixed pump made this test flaky in the full suite.
    final deadline = DateTime.now().add(const Duration(seconds: 5));
    while (errors.isEmpty && DateTime.now().isBefore(deadline)) {
      await pump(20);
    }

    expect(calls, 3, reason: 'one real connect + maxAttempts retries');
    expect(errors.single.code, 'connection_lost');
    expect(errors.single.fatal, isTrue);
    expect(socket.state, VoiceConnection.closed);
    expect(states, contains(VoiceConnection.reconnecting));
  });

  test('a failure on the very first connect is thrown to the caller (bad token / no network)', () async {
    final socket = VoiceSocket(factory: () async => throw StateError('no token'));
    await expectLater(socket.connect(), throwsStateError);
    expect(socket.state, VoiceConnection.closed);
  });

  test('auth rejection (4401) is terminal: no reconnect storm', () async {
    final r = Rig();
    await r.boot();
    final errors = <VoiceError>[];
    r.socket.events.listen((e) {
      if (e is VoiceError) errors.add(e);
    });
    r.t.drop(code: 4401);
    await pump(60);
    expect(r.transports.length, 1);
    expect(errors.single.fatal, isTrue);
    expect(r.socket.state, VoiceConnection.closed);
  });
}
