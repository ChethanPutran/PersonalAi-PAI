import 'dart:convert';

/// Typed mirror of the backend voice wire protocol
/// (`src/pai/voice/models.py`). Binary frames are raw PCM16LE mono and are
/// handled by [VoiceSocket]; everything here is the JSON text frames.

enum VoiceState { idle, listening, thinking, speaking }

VoiceState _stateFrom(String? s) => switch (s) {
      'listening' => VoiceState.listening,
      'thinking' => VoiceState.thinking,
      'speaking' => VoiceState.speaking,
      _ => VoiceState.idle,
    };

enum InterruptReason { bargeIn, cancel, newInput, unknown }

InterruptReason _reasonFrom(String? s) => switch (s) {
      'barge_in' => InterruptReason.bargeIn,
      'cancel' => InterruptReason.cancel,
      'new_input' => InterruptReason.newInput,
      _ => InterruptReason.unknown,
    };

// ---------------------------------------------------------------------------
// server -> client
// ---------------------------------------------------------------------------

sealed class VoiceServerEvent {
  const VoiceServerEvent();

  /// Parses one JSON text frame. Returns `null` for malformed JSON (the
  /// caller logs and ignores it); unknown `type`s become [UnknownVoiceEvent]
  /// so a newer server never breaks an older client.
  static VoiceServerEvent? parse(String raw) {
    final Object? decoded;
    try {
      decoded = jsonDecode(raw);
    } on FormatException {
      return null;
    }
    if (decoded is! Map<String, dynamic>) return null;
    final j = decoded;
    final type = j['type'];
    if (type is! String) return null;

    String s(String k) => j[k]?.toString() ?? '';
    String? sn(String k) => j[k]?.toString();

    switch (type) {
      case 'session.ready':
        final limits = (j['limits'] as Map?) ?? const {};
        final caps = (j['capabilities'] as Map?) ?? const {};
        return SessionReady(
          voiceSessionId: s('voice_session_id'),
          chatSessionId: s('chat_session_id'),
          inputSampleRate: (j['input_sample_rate'] as num?)?.toInt() ?? 16000,
          outputSampleRate: (j['output_sample_rate'] as num?)?.toInt() ?? 24000,
          maxUtteranceSeconds:
              (limits['max_utterance_seconds'] as num?)?.toDouble() ?? 30,
          idleTimeoutSeconds:
              (limits['idle_timeout_seconds'] as num?)?.toDouble() ?? 120,
          maxSessionSeconds:
              (limits['max_session_seconds'] as num?)?.toDouble() ?? 3600,
          stt: caps['stt']?.toString() ?? '',
          tts: caps['tts']?.toString() ?? '',
          partialTranscripts: caps['partial_transcripts'] == true,
          bargeIn: caps['barge_in'] == true,
        );
      case 'state':
        return StateChanged(_stateFrom(sn('state')), turnId: sn('turn_id'));
      case 'speech.started':
        return SpeechStarted(s('turn_id'));
      case 'speech.stopped':
        return SpeechStopped(s('turn_id'));
      case 'transcript.partial':
        return TranscriptPartial(s('turn_id'), s('text'));
      case 'transcript.final':
        return TranscriptFinal(s('turn_id'), s('text'));
      case 'assistant.status':
        return AssistantStatus(s('turn_id'), s('stage'), s('message'));
      case 'assistant.text.delta':
        return AssistantTextDelta(s('turn_id'), s('response_id'), s('delta'));
      case 'assistant.text.done':
        return AssistantTextDone(s('turn_id'), s('response_id'), s('text'));
      case 'assistant.audio.start':
        return AssistantAudioStart(
          s('response_id'),
          (j['sample_rate'] as num?)?.toInt() ?? 24000,
        );
      case 'assistant.audio.end':
        return AssistantAudioEnd(s('response_id'));
      case 'assistant.interrupted':
        return AssistantInterrupted(
          responseId: sn('response_id'),
          reason: _reasonFrom(sn('reason')),
          spokenText: s('spoken_text'),
        );
      case 'error':
        return VoiceError(
          code: s('code'),
          message: s('message'),
          fatal: j['fatal'] == true,
        );
      case 'pong':
        return Pong(sn('id'));
      case 'session.ended':
        return SessionEnded(s('reason'));
      default:
        return UnknownVoiceEvent(type);
    }
  }
}

class SessionReady extends VoiceServerEvent {
  final String voiceSessionId;
  final String chatSessionId;
  final int inputSampleRate;
  final int outputSampleRate;
  final double maxUtteranceSeconds;
  final double idleTimeoutSeconds;
  final double maxSessionSeconds;
  final String stt;
  final String tts;
  final bool partialTranscripts;
  final bool bargeIn;
  const SessionReady({
    required this.voiceSessionId,
    required this.chatSessionId,
    required this.inputSampleRate,
    required this.outputSampleRate,
    required this.maxUtteranceSeconds,
    required this.idleTimeoutSeconds,
    required this.maxSessionSeconds,
    required this.stt,
    required this.tts,
    required this.partialTranscripts,
    required this.bargeIn,
  });
}

class StateChanged extends VoiceServerEvent {
  final VoiceState state;
  final String? turnId;
  const StateChanged(this.state, {this.turnId});
}

class SpeechStarted extends VoiceServerEvent {
  final String turnId;
  const SpeechStarted(this.turnId);
}

class SpeechStopped extends VoiceServerEvent {
  final String turnId;
  const SpeechStopped(this.turnId);
}

class TranscriptPartial extends VoiceServerEvent {
  final String turnId;
  final String text;
  const TranscriptPartial(this.turnId, this.text);
}

class TranscriptFinal extends VoiceServerEvent {
  final String turnId;
  final String text;
  const TranscriptFinal(this.turnId, this.text);
}

class AssistantStatus extends VoiceServerEvent {
  final String turnId;
  final String stage;
  final String message;
  const AssistantStatus(this.turnId, this.stage, this.message);
}

class AssistantTextDelta extends VoiceServerEvent {
  final String turnId;
  final String responseId;
  final String delta;
  const AssistantTextDelta(this.turnId, this.responseId, this.delta);
}

class AssistantTextDone extends VoiceServerEvent {
  final String turnId;
  final String responseId;
  final String text;
  const AssistantTextDone(this.turnId, this.responseId, this.text);
}

class AssistantAudioStart extends VoiceServerEvent {
  final String responseId;
  final int sampleRate;
  const AssistantAudioStart(this.responseId, this.sampleRate);
}

class AssistantAudioEnd extends VoiceServerEvent {
  final String responseId;
  const AssistantAudioEnd(this.responseId);
}

class AssistantInterrupted extends VoiceServerEvent {
  final String? responseId;
  final InterruptReason reason;
  final String spokenText;
  const AssistantInterrupted({
    this.responseId,
    required this.reason,
    this.spokenText = '',
  });
}

class VoiceError extends VoiceServerEvent {
  final String code;
  final String message;
  final bool fatal;
  const VoiceError({
    required this.code,
    required this.message,
    this.fatal = false,
  });
}

class Pong extends VoiceServerEvent {
  final String? id;
  const Pong(this.id);
}

class SessionEnded extends VoiceServerEvent {
  final String reason;
  const SessionEnded(this.reason);
}

class UnknownVoiceEvent extends VoiceServerEvent {
  final String type;
  const UnknownVoiceEvent(this.type);
}

// ---------------------------------------------------------------------------
// client -> server
// ---------------------------------------------------------------------------

sealed class VoiceClientMessage {
  const VoiceClientMessage();
  Map<String, Object?> toJson();
  String encode() => jsonEncode(toJson());
}

class SessionStartMsg extends VoiceClientMessage {
  final String? sessionId;
  final String? deviceId;
  final String? language;
  final String? voice;
  final int? inputSampleRate;
  final String clientName;
  final String clientVersion;
  const SessionStartMsg({
    this.sessionId,
    this.deviceId,
    this.language,
    this.voice,
    this.inputSampleRate,
    this.clientName = 'pai_mobile',
    this.clientVersion = '1.0.0',
  });

  SessionStartMsg copyWith({String? sessionId}) => SessionStartMsg(
        sessionId: sessionId ?? this.sessionId,
        deviceId: deviceId,
        language: language,
        voice: voice,
        inputSampleRate: inputSampleRate,
        clientName: clientName,
        clientVersion: clientVersion,
      );

  @override
  Map<String, Object?> toJson() => {
        'type': 'session.start',
        if (sessionId != null) 'session_id': sessionId,
        if (deviceId != null) 'device_id': deviceId,
        if (language != null) 'language': language,
        if (voice != null) 'voice': voice,
        if (inputSampleRate != null) 'input_sample_rate': inputSampleRate,
        'client': {'name': clientName, 'version': clientVersion},
      };
}

class SessionUpdateMsg extends VoiceClientMessage {
  final String? language;
  final String? voice;
  const SessionUpdateMsg({this.language, this.voice});
  @override
  Map<String, Object?> toJson() => {
        'type': 'session.update',
        if (language != null) 'language': language,
        if (voice != null) 'voice': voice,
      };
}

class InputTextMsg extends VoiceClientMessage {
  final String text;
  const InputTextMsg(this.text);
  @override
  Map<String, Object?> toJson() => {'type': 'input.text', 'text': text};
}

class ResponseCancelMsg extends VoiceClientMessage {
  const ResponseCancelMsg();
  @override
  Map<String, Object?> toJson() => {'type': 'response.cancel'};
}

class PingMsg extends VoiceClientMessage {
  final String? id;
  const PingMsg([this.id]);
  @override
  Map<String, Object?> toJson() => {'type': 'ping', if (id != null) 'id': id};
}

class SessionEndMsg extends VoiceClientMessage {
  const SessionEndMsg();
  @override
  Map<String, Object?> toJson() => {'type': 'session.end'};
}
