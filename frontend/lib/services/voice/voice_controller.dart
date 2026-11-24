import 'dart:async';
import 'dart:math' as math;
import 'package:flutter/foundation.dart';

import '../../chat/chat_service.dart';
import '../../core/api/api_client.dart';
import 'audio_io.dart';
import 'voice_events.dart';
import 'voice_socket.dart';

/// Drives a realtime voice conversation and exposes everything the UI needs.
///
/// State machine (server-authoritative, via `state` events):
///   idle ─speech.started→ listening ─transcript.final→ thinking
///        ─assistant.audio.start→ speaking ─audio.end→ idle
///   speaking/thinking ─assistant.interrupted→ idle   (queued audio is flushed)
///
/// Audio rules: binary frames are only played between `assistant.audio.start`
/// and `assistant.audio.end`/`assistant.interrupted`; frames that were already
/// in flight when an interruption happened are dropped.
class VoiceController extends ChangeNotifier {
  final VoiceSocket _socket;
  final AudioInput _mic;
  final AudioOutput _player;
  final Duration _pingEvery;

  VoiceController({
    required VoiceSocket socket,
    required AudioInput mic,
    required AudioOutput player,
    Duration pingEvery = const Duration(seconds: 20),
    this.onChatSession,
  })  : _socket = socket,
        _mic = mic,
        _player = player,
        _pingEvery = pingEvery;

  /// Production wiring from the app's [ApiClient] / [ChatService].
  factory VoiceController.create({
    required ApiClient api,
    required ChatService chat,
    required String deviceId,
    String? language,
    String? voice,
  }) {
    final mic = MicCapture();
    return VoiceController(
      socket: VoiceSocket(
        factory: voiceTransportFor(api),
        start: SessionStartMsg(
          sessionId: chat.sessionId,
          deviceId: deviceId,
          language: language,
          voice: voice,
          inputSampleRate: mic.sampleRate,
        ),
      ),
      mic: mic,
      player: PcmPlayer(),
      // Voice turns are stored in the same chat session; adopt its id so the
      // text chat shows them and keeps going from there.
      onChatSession: (id) => chat.setSessionId(id),
    );
  }

  /// Called with the chat session id the server attached this voice session to.
  final void Function(String chatSessionId)? onChatSession;

  // ---- observable state ----------------------------------------------------
  VoiceState state = VoiceState.idle;
  VoiceConnection connection = VoiceConnection.idle;
  bool ready = false;
  bool muted = false;
  String userPartial = '';
  String userText = '';
  String assistantText = '';
  String statusMessage = '';
  String? error;
  String? chatSessionId;
  bool bargeInSupported = true;

  /// 0..1, smoothed: the user's mic while listening, the assistant's voice while speaking.
  double get level => state == VoiceState.speaking ? _outLevel : _inLevel;

  /// What the user sees as "my words so far" — live partial, else the last final.
  String get userCaption => userPartial.isNotEmpty ? userPartial : userText;

  bool get active => ready && connection == VoiceConnection.connected;

  // ---- internals -------------------------------------------------------------
  final List<StreamSubscription<Object?>> _subs = [];
  StreamSubscription<Uint8List>? _micSub;
  Timer? _pingTimer;
  Timer? _levelTimer;
  bool _responseOpen = false;
  bool _started = false;
  bool _disposed = false;
  double _inLevel = 0;
  double _outLevel = 0;

  // ---------------------------------------------------------------------------

  Future<void> start() async {
    if (_started || _disposed) return;
    _started = true;
    error = null;

    if (!await _mic.ensurePermission()) {
      error = 'Microphone permission is required for voice.';
      _started = false;
      _notify();
      return;
    }

    _subs
      ..add(_socket.events.listen(handleEvent))
      ..add(_socket.audio.listen(handleAudio))
      ..add(_socket.connection.listen((c) {
        connection = c;
        if (c != VoiceConnection.connected) ready = false;
        _notify();
      }));

    try {
      await _socket.connect();
    } catch (e) {
      error = 'Could not start voice: $e';
      _started = false;
      _notify();
      return;
    }

    _pingTimer = Timer.periodic(_pingEvery, (_) => _socket.send(const PingMsg()));
    // Smooths the orb and decays levels when no frames arrive.
    _levelTimer = Timer.periodic(const Duration(milliseconds: 50), (_) {
      _inLevel *= 0.85;
      _outLevel = math.max(_player.outputLevel, _outLevel * 0.85);
      if (state == VoiceState.listening || state == VoiceState.speaking) _notify();
    });
  }

  /// Applies one server event. Public so tests can drive the state machine.
  @visibleForTesting
  void handleEvent(VoiceServerEvent ev) {
    switch (ev) {
      case SessionReady():
        ready = true;
        chatSessionId = ev.chatSessionId;
        bargeInSupported = ev.bargeIn;
        if (ev.chatSessionId.isNotEmpty) onChatSession?.call(ev.chatSessionId);
        unawaited(_onReady(ev));
      case StateChanged():
        state = ev.state;
        if (ev.state == VoiceState.idle) statusMessage = '';
      case SpeechStarted():
        userPartial = '';
        error = null;
      case SpeechStopped():
        break;
      case TranscriptPartial():
        userPartial = ev.text;
      case TranscriptFinal():
        userText = ev.text;
        userPartial = '';
        assistantText = ''; // a new turn begins: clear the previous reply caption
        statusMessage = '';
      case AssistantStatus():
        statusMessage = ev.message.isNotEmpty ? ev.message : ev.stage;
      case AssistantTextDelta():
        assistantText += ev.delta;
      case AssistantTextDone():
        assistantText = ev.text;
      case AssistantAudioStart():
        _responseOpen = true;
        unawaited(_player.configure(ev.sampleRate));
      case AssistantAudioEnd():
        _responseOpen = false;
        _player.endResponse();
      case AssistantInterrupted():
        _responseOpen = false;
        unawaited(_player.flush()); // the user must hear silence immediately
        if (ev.spokenText.isNotEmpty) assistantText = ev.spokenText;
        _outLevel = 0;
      case VoiceError():
        error = ev.message;
        if (ev.fatal) unawaited(stop());
      case SessionEnded():
        ready = false;
        unawaited(stop());
      case Pong():
      case UnknownVoiceEvent():
        break;
    }
    _notify();
  }

  /// Applies one binary audio frame from the server.
  @visibleForTesting
  void handleAudio(Uint8List pcm) {
    if (!_responseOpen) return; // late frame of an interrupted/finished response
    _player.feed(pcm);
  }

  Future<void> _onReady(SessionReady ev) async {
    if (_micSub != null || muted || _disposed) return; // already streaming (reconnect) or muted
    await _startMic();
  }

  Future<void> _startMic() async {
    try {
      final frames = await _mic.start();
      _micSub = frames.listen(
        (f) {
          _inLevel = math.max(_inLevel * 0.5, math.min(1.0, pcmLevel(f) * 6));
          if (active && !muted) _socket.sendAudio(f);
        },
        onError: (Object e) {
          error = 'Microphone error: $e';
          _notify();
        },
      );
    } catch (e) {
      error = 'Could not open the microphone: $e';
      _notify();
    }
  }

  // ---- user actions ------------------------------------------------------------

  /// Mute really stops the recorder (the OS mic indicator goes off), it does not
  /// just stop sending.
  Future<void> setMuted(bool value) async {
    if (muted == value) return;
    muted = value;
    _notify();
    if (value) {
      await _micSub?.cancel();
      _micSub = null;
      await _mic.stop();
      _inLevel = 0;
    } else if (ready) {
      await _startMic();
    }
    _notify();
  }

  Future<void> toggleMute() => setMuted(!muted);

  /// Stop the assistant talking right now (the orb / stop button).
  void interrupt() {
    _responseOpen = false;
    unawaited(_player.flush()); // local, instant — don't wait for the server round trip
    _socket.send(const ResponseCancelMsg());
  }

  /// Type a message inside the voice session; it is answered with voice too.
  void sendText(String text) {
    final t = text.trim();
    if (t.isEmpty) return;
    userText = t;
    userPartial = '';
    assistantText = '';
    _socket.send(InputTextMsg(t));
    _notify();
  }

  Future<void> stop() async {
    if (!_started) return;
    _started = false;
    _pingTimer?.cancel();
    _levelTimer?.cancel();
    await _micSub?.cancel();
    _micSub = null;
    await _mic.stop();
    for (final s in _subs) {
      await s.cancel();
    }
    _subs.clear();
    await _socket.close();
    await _player.flush();
    ready = false;
    state = VoiceState.idle;
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(stop().then((_) async {
      await _mic.dispose();
      await _player.dispose();
    }));
    super.dispose();
  }
}
