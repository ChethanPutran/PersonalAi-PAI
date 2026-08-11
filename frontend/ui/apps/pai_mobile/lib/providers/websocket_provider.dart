import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/legacy.dart';
import '../services/websocket_service.dart';
import '../models/message.dart';

final websocketProvider = Provider<WebSocketService>((ref) {
  final service = WebSocketService(
    onMessage: (msg) {
      ref.read(messageProvider.notifier).addMessage(msg);
    },
  );
  service.connect();
  ref.onDispose(() => service.disconnect());
  return service;
});

final messageProvider = StateNotifierProvider<MessageNotifier, List<AIMessage>>((ref) {
  return MessageNotifier();
});

class MessageNotifier extends StateNotifier<List<AIMessage>> {
  MessageNotifier() : super([]);

  void addMessage(AIMessage msg) {
    state = [...state, msg];
  }

  void clear() {
    state = [];
  }
}