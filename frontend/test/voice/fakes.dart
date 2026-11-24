import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:pai_mobile/services/voice/audio_io.dart';
import 'package:pai_mobile/services/voice/voice_socket.dart';

/// Server side of a fake WebSocket: tests push frames in, inspect what the client sent.
class FakeTransport implements VoiceTransport {
  final _in = StreamController<Object?>();
  final sentText = <Map<String, dynamic>>[];
  final sentBinary = <Uint8List>[];
  @override
  int? closeCode;
  bool closed = false;

  @override
  Stream<Object?> get incoming => _in.stream;
  @override
  void sendText(String data) => sentText.add(jsonDecode(data) as Map<String, dynamic>);
  @override
  void sendBinary(Uint8List data) => sentBinary.add(data);
  @override
  Future<void> close() async {
    closed = true;
    if (!_in.isClosed) await _in.close();
  }

  // server -> client
  void serverJson(Map<String, Object?> m) => _in.add(jsonEncode(m));
  void serverAudio(List<int> pcm) => _in.add(Uint8List.fromList(pcm));
  void drop({int? code}) {
    closeCode = code;
    _in.close();
  }

  List<String> get sentTypes => sentText.map((m) => m['type'] as String).toList();
}

class FakeMic implements AudioInput {
  bool permission = true;
  bool running = false;
  int startCount = 0;
  StreamController<Uint8List>? _c;

  @override
  Future<bool> ensurePermission() async => permission;
  @override
  Future<Stream<Uint8List>> start() async {
    running = true;
    startCount++;
    _c = StreamController<Uint8List>();
    return _c!.stream;
  }

  void frame(Uint8List f) => _c?.add(f);

  @override
  Future<void> stop() async {
    running = false;
    await _c?.close();
    _c = null;
  }

  @override
  Future<void> dispose() => stop();
}

class FakePlayer implements AudioOutput {
  final fed = <Uint8List>[];
  int flushes = 0;
  int ends = 0;
  int? configuredRate;
  @override
  Future<void> configure(int sampleRate) async => configuredRate = sampleRate;
  @override
  void feed(Uint8List pcm) => fed.add(pcm);
  @override
  Future<void> flush() async => flushes++;
  @override
  void endResponse() => ends++;
  @override
  int get playedMs => 0;
  @override
  int get bufferedMs => 0;
  @override
  double get outputLevel => 0;
  @override
  Future<void> dispose() async {}
}

Map<String, Object?> readyEvent({String chat = 'chat-1'}) => {
      'type': 'session.ready',
      'voice_session_id': 'v1',
      'chat_session_id': chat,
      'input_sample_rate': 16000,
      'output_sample_rate': 24000,
      'output_format': 'pcm_s16le',
      'limits': {
        'max_utterance_seconds': 30.0,
        'idle_timeout_seconds': 120.0,
        'max_session_seconds': 3600.0,
      },
      'capabilities': {
        'stt': 'fake',
        'tts': 'fake',
        'partial_transcripts': true,
        'barge_in': true,
      },
    };

Future<void> pump([int ms = 20]) => Future<void>.delayed(Duration(milliseconds: ms));
