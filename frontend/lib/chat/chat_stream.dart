import 'dart:convert';

import '../models/chat.dart';
import 'sse_client.dart';

/// Typed events of `POST /chat/stream`:
///   event `status` {stage,message} · `delta` {delta} · `done` {…ChatResponse} · `error` {message}
sealed class ChatStreamEvent {
  const ChatStreamEvent();

  /// Returns `null` for events this client doesn't know (forward compatible) or
  /// whose payload is not valid JSON.
  static ChatStreamEvent? fromSse(SseEvent e) {
    final Object? json;
    try {
      json = e.data.isEmpty ? const <String, Object?>{} : jsonDecode(e.data);
    } on FormatException {
      return null;
    }
    if (json is! Map<String, dynamic>) return null;
    switch (e.event) {
      case 'status':
        return ChatStreamStatus(
          stage: json['stage']?.toString() ?? '',
          message: json['message']?.toString() ?? '',
        );
      case 'delta':
        return ChatStreamDelta(json['delta']?.toString() ?? '');
      case 'done':
        return ChatStreamDone(ChatResponse.fromJson(json));
      case 'error':
        return ChatStreamError(json['message']?.toString() ?? 'Unknown error');
      default:
        return null;
    }
  }
}

class ChatStreamStatus extends ChatStreamEvent {
  final String stage;
  final String message;
  const ChatStreamStatus({required this.stage, required this.message});
}

class ChatStreamDelta extends ChatStreamEvent {
  final String delta;
  const ChatStreamDelta(this.delta);
}

class ChatStreamDone extends ChatStreamEvent {
  final ChatResponse response;
  const ChatStreamDone(this.response);
}

class ChatStreamError extends ChatStreamEvent {
  final String message;
  const ChatStreamError(this.message);
}
