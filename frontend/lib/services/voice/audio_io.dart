import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/foundation.dart';
import 'package:flutter_pcm_sound/flutter_pcm_sound.dart';
import 'package:record/record.dart';

/// RMS level (0..1) of PCM16LE samples.
double pcmLevel(Uint8List pcm) {
  final n = pcm.lengthInBytes ~/ 2;
  if (n == 0) return 0;
  final bd = ByteData.sublistView(pcm);
  double sum = 0;
  for (var i = 0; i < n; i++) {
    final v = bd.getInt16(i * 2, Endian.little) / 32768.0;
    sum += v * v;
  }
  return math.sqrt(sum / n);
}

// ---------------------------------------------------------------------------
// Microphone
// ---------------------------------------------------------------------------

abstract class AudioInput {
  /// Asks for (or checks) the microphone permission.
  Future<bool> ensurePermission();

  /// Streams PCM16LE mono at [sampleRate] in ~[frameMs] ms frames.
  Future<Stream<Uint8List>> start();
  Future<void> stop();
  Future<void> dispose();
}

/// Microphone capture via the `record` package.
///
/// `record` is used because it is the one maintained plugin that streams raw
/// PCM16 and exposes the platform's echo canceller / noise suppressor / AGC
/// (needed so the assistant's own voice is not picked up as a barge-in).
class MicCapture implements AudioInput {
  final int sampleRate;
  final int frameMs;
  final AudioRecorder _rec = AudioRecorder();
  StreamSubscription<Uint8List>? _sub;
  StreamController<Uint8List>? _out;

  MicCapture({this.sampleRate = 16000, this.frameMs = 40});

  @override
  Future<bool> ensurePermission() => _rec.hasPermission();

  @override
  Future<Stream<Uint8List>> start() async {
    await stop();
    final raw = await _rec.startStream(RecordConfig(
      encoder: AudioEncoder.pcm16bits,
      sampleRate: sampleRate,
      numChannels: 1,
      echoCancel: true,
      noiseSuppress: true,
      autoGain: true,
    ));

    // The plugin's chunk size is platform dependent; re-frame to a steady size.
    final frameBytes = sampleRate * frameMs ~/ 1000 * 2;
    final pending = BytesBuilder(copy: false);
    final out = StreamController<Uint8List>();
    _out = out;
    _sub = raw.listen(
      (chunk) {
        pending.add(chunk);
        while (pending.length >= frameBytes) {
          final all = pending.takeBytes();
          out.add(Uint8List.sublistView(all, 0, frameBytes));
          if (all.length > frameBytes) {
            pending.add(Uint8List.sublistView(all, frameBytes));
          }
        }
      },
      onError: out.addError,
      onDone: () {
        if (!out.isClosed) out.close();
      },
    );
    return out.stream;
  }

  @override
  Future<void> stop() async {
    await _sub?.cancel();
    _sub = null;
    if (await _rec.isRecording()) {
      await _rec.stop();
    }
    final out = _out;
    _out = null;
    if (out != null && !out.isClosed) await out.close();
  }

  @override
  Future<void> dispose() async {
    await stop();
    await _rec.dispose();
  }
}

// ---------------------------------------------------------------------------
// Playback
// ---------------------------------------------------------------------------

abstract class AudioOutput {
  Future<void> configure(int sampleRate);

  /// Queue PCM16LE mono for playback (arrives in network-sized bursts).
  void feed(Uint8List pcm);

  /// Drop everything not yet played (barge-in). Must be fast.
  Future<void> flush();

  /// The response's audio is complete; playback drains what is buffered.
  void endResponse();

  /// Milliseconds of audio actually played since the last [flush]/configure.
  int get playedMs;

  /// Milliseconds buffered but not yet played.
  int get bufferedMs;

  /// 0..1 level of what is currently being played (for the orb).
  double get outputLevel;

  Future<void> dispose();
}

/// Streaming PCM playback via `flutter_pcm_sound`.
///
/// Why this package: it is the only maintained plugin that plays a *continuous
/// raw PCM stream* with a pull-style feed callback (no WAV files, no
/// per-chunk players). It has no `flush()`, so we keep our own jitter buffer
/// in Dart and only hand the native player small slices when it asks for more.
/// What is already inside the native buffer is therefore at most
/// ~[feedThresholdMs] + [sliceMs], which bounds the cut-off delay on barge-in.
/// Not supported on Linux/Windows (no platform implementation).
class PcmPlayer implements AudioOutput {
  final int prebufferMs;
  final int sliceMs;
  final int feedThresholdMs;

  PcmPlayer({this.prebufferMs = 120, this.sliceMs = 40, this.feedThresholdMs = 60});

  int _rate = 24000;
  bool _ready = false;
  bool _started = false;
  bool _ended = false;
  final List<Uint8List> _chunks = [];
  int _queuedBytes = 0;

  int _fedFrames = 0;
  int _nativeRemaining = 0;
  double _level = 0;

  int get _bytesPerMs => _rate * 2 ~/ 1000;

  @override
  Future<void> configure(int sampleRate) async {
    if (_ready && sampleRate == _rate) {
      await flush();
      return;
    }
    _rate = sampleRate;
    await FlutterPcmSound.setup(sampleRate: sampleRate, channelCount: 1);
    await FlutterPcmSound.setFeedThreshold(sampleRate * feedThresholdMs ~/ 1000);
    FlutterPcmSound.setFeedCallback(_onFeed);
    _ready = true;
    await flush();
  }

  @override
  void feed(Uint8List pcm) {
    if (!_ready || pcm.isEmpty) return;
    _ended = false;
    _chunks.add(pcm);
    _queuedBytes += pcm.length;
    _level = pcmLevel(pcm);
    if (!_started && _queuedBytes >= prebufferMs * _bytesPerMs) {
      _started = true;
      FlutterPcmSound.start();
    }
  }

  @override
  void endResponse() {
    _ended = true;
    // A short response may never reach the prebuffer: play it anyway.
    if (!_started && _queuedBytes > 0) {
      _started = true;
      FlutterPcmSound.start();
    }
  }

  void _onFeed(int remainingFrames) {
    _nativeRemaining = remainingFrames;
    final want = sliceMs * _bytesPerMs;
    if (_queuedBytes == 0) {
      _level *= 0.6;
      if (_ended) _started = false; // drained; the next response re-prebuffers
      return;
    }
    final take = math.min(want, _queuedBytes) & ~1;
    final slice = _take(take);
    _fedFrames += slice.length ~/ 2;
    FlutterPcmSound.feed(PcmArrayInt16(bytes: ByteData.sublistView(slice)));
  }

  Uint8List _take(int n) {
    final out = Uint8List(n);
    var filled = 0;
    while (filled < n && _chunks.isNotEmpty) {
      final c = _chunks.first;
      final need = n - filled;
      if (c.length <= need) {
        out.setRange(filled, filled + c.length, c);
        filled += c.length;
        _chunks.removeAt(0);
      } else {
        out.setRange(filled, n, c);
        _chunks[0] = Uint8List.sublistView(c, need);
        filled = n;
      }
    }
    _queuedBytes -= n;
    return out;
  }

  @override
  Future<void> flush() async {
    _chunks.clear();
    _queuedBytes = 0;
    _started = false;
    _ended = false;
    _fedFrames = 0;
    _nativeRemaining = 0;
    _level = 0;
  }

  @override
  int get playedMs {
    final played = math.max(0, _fedFrames - _nativeRemaining);
    return played * 1000 ~/ _rate;
  }

  @override
  int get bufferedMs => (_queuedBytes ~/ 2) * 1000 ~/ _rate;

  @override
  double get outputLevel => _level;

  @override
  Future<void> dispose() async {
    await flush();
    FlutterPcmSound.setFeedCallback(null);
    try {
      await FlutterPcmSound.release();
    } catch (e) {
      debugPrint('[PcmPlayer] release failed: $e');
    }
    _ready = false;
  }
}
