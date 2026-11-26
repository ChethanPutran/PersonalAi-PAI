import 'dart:async';
import 'dart:io' show Platform, Process, ProcessSignal;
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
  bool _starved = false; // the speaker ran dry between sentences: the next frames must restart the feed loop
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
    // Do NOT flush here: the first audio frames of the reply usually arrive while the speaker is still being
    // set up. They are queued by feed(); start playing them now (a short one-sentence reply would otherwise
    // never be heard, only shown as text).
    if (!_started && _queuedBytes > 0 && (_ended || _queuedBytes >= prebufferMs * _bytesPerMs)) {
      _started = true;
      FlutterPcmSound.start();
    }
  }

  @override
  void feed(Uint8List pcm) {
    if (pcm.isEmpty) return;
    _ended = false;
    _chunks.add(pcm);                // queued even before the speaker is ready (see configure)
    _queuedBytes += pcm.length;
    _level = pcmLevel(pcm);
    if (_ready && _started && _starved) {
      _starved = false;
      _onFeed(_nativeRemaining); // no feed callback fires while nothing is fed: prime it by hand
    }
    if (_ready && !_started && _queuedBytes >= prebufferMs * _bytesPerMs) {
      _started = true;
      FlutterPcmSound.start();
    }
  }

  @override
  void endResponse() {
    _ended = true;
    // A short response may never reach the prebuffer: play it anyway.
    if (_ready && !_started && _queuedBytes > 0) {
      _started = true;
      FlutterPcmSound.start();
    }
  }

  void _onFeed(int remainingFrames) {
    _nativeRemaining = remainingFrames;
    final want = sliceMs * _bytesPerMs;
    if (_queuedBytes == 0) {
      _level *= 0.6;
      if (_ended) {
        _started = false; // drained; the next response re-prebuffers
      } else {
        _starved = true; // between sentences (the next one is still being synthesised)
      }
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
    _starved = false;
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

/// The right speaker backend for this platform: `flutter_pcm_sound` has no Linux/Windows implementation
/// (`MissingPluginException ... setup`), so the Linux desktop app plays through the system sound server instead.
AudioOutput createAudioOutput() => (!kIsWeb && Platform.isLinux) ? ProcessPcmPlayer() : PcmPlayer();

/// Linux playback: streams the PCM into `paplay` (PulseAudio / PipeWire), `pw-cat` or `aplay`, whichever exists.
///
/// Barge-in = kill the process (everything inside the pipe and the sound server's buffer is dropped); the next
/// response starts a new one. The pipe's own back-pressure keeps only ~1 s of audio ahead of the speaker.
class ProcessPcmPlayer implements AudioOutput {
  final int sliceMs;
  ProcessPcmPlayer({this.sliceMs = 40});

  int _rate = 24000;
  Process? _proc;
  final List<Uint8List> _chunks = [];
  int _queuedBytes = 0;
  int _writtenBytes = 0;
  DateTime? _t0;
  bool _ended = false;
  bool _pumping = false;
  int _gen = 0; // bumped by flush(): a pump that was running for an older generation stops
  double _level = 0;
  String? _tool;

  int get _bytesPerMs => _rate * 2 ~/ 1000;

  static Future<List<String>?> _command(int rate) async {
    final r = '$rate';
    final options = <List<String>>[
      ['paplay', '--raw', '--format=s16le', '--rate=$r', '--channels=1', '--latency-msec=60'],
      ['pw-cat', '--playback', '--raw', '--format', 's16', '--rate', r, '--channels', '1', '-'],
      ['aplay', '-q', '-t', 'raw', '-f', 'S16_LE', '-r', r, '-c', '1', '-'],
    ];
    for (final o in options) {
      final found = await Process.run('which', [o.first]);
      if (found.exitCode == 0) return o;
    }
    return null;
  }

  @override
  Future<void> configure(int sampleRate) async {
    if (sampleRate != _rate) _killProcess();
    _rate = sampleRate;
    await flush();
  }

  Future<bool> _ensureProcess() async {
    if (_proc != null) return true;
    final cmd = await _command(_rate);
    if (cmd == null) {
      debugPrint('[ProcessPcmPlayer] no player found: install pulseaudio-utils (paplay), pipewire (pw-cat) or alsa-utils (aplay)');
      return false;
    }
    try {
      final proc = await Process.start(cmd.first, cmd.sublist(1));
      _tool = cmd.first;
      proc.stdout.drain<void>();
      proc.stderr.drain<void>();
      unawaited(proc.exitCode.then((_) {
        if (identical(_proc, proc)) _proc = null;
      }));
      _proc = proc;
      _writtenBytes = 0;
      _t0 = null;
      return true;
    } catch (e) {
      debugPrint('[ProcessPcmPlayer] could not start ${cmd.first}: $e');
      return false;
    }
  }

  void _killProcess() {
    final p = _proc;
    _proc = null;
    p?.kill(ProcessSignal.sigkill);
  }

  @override
  void feed(Uint8List pcm) {
    if (pcm.isEmpty) return;
    _ended = false;
    _chunks.add(pcm);
    _queuedBytes += pcm.length;
    _level = pcmLevel(pcm);
    if (!_pumping) unawaited(_pump());
  }

  Future<void> _pump() async {
    _pumping = true;
    final gen = _gen;
    try {
      if (!await _ensureProcess() || gen != _gen) return;
      while (_queuedBytes > 0 && gen == _gen) {
        final proc = _proc;
        if (proc == null) break;
        final n = math.min(sliceMs * _bytesPerMs, _queuedBytes) & ~1;
        final slice = _take(n);
        _t0 ??= DateTime.now();
        _writtenBytes += slice.length;
        proc.stdin.add(slice);
        await proc.stdin.flush(); // back-pressure: waits while the pipe is full
      }
      if (gen == _gen && _ended && _queuedBytes == 0) {
        final proc = _proc;
        _proc = null; // let it finish playing what it has, then exit
        await proc?.stdin.close();
      }
    } catch (e) {
      debugPrint('[ProcessPcmPlayer] playback stopped: $e'); // e.g. the process was killed by a barge-in
    } finally {
      _pumping = false;
      if (gen == _gen && _queuedBytes > 0) unawaited(_pump()); // frames arrived while stopping
    }
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
  void endResponse() {
    _ended = true;
    if (!_pumping && _proc != null && _queuedBytes == 0) unawaited(_pump());
  }

  @override
  Future<void> flush() async {
    _gen++;
    _chunks.clear();
    _queuedBytes = 0;
    _ended = false;
    _level = 0;
    _killProcess();
    _writtenBytes = 0;
    _t0 = null;
  }

  int get _writtenMs => _writtenBytes ~/ math.max(1, _bytesPerMs);

  @override
  int get playedMs {
    final t0 = _t0;
    if (t0 == null) return 0;
    return math.min(_writtenMs, DateTime.now().difference(t0).inMilliseconds);
  }

  @override
  int get bufferedMs => _queuedBytes ~/ math.max(1, _bytesPerMs) + math.max(0, _writtenMs - playedMs);

  @override
  double get outputLevel {
    _level *= bufferedMs > 0 ? 0.98 : 0.6;
    return _level;
  }

  @override
  Future<void> dispose() async {
    await flush();
    debugPrint('[ProcessPcmPlayer] disposed (${_tool ?? 'never started'})');
  }
}
