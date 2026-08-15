import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/legacy.dart';
import 'package:riverpod/riverpod.dart';  // Ensures StateNotifier & StateNotifierProvider are available
import 'package:flutter/material.dart';
import '../main.dart'; // for scaffoldMessengerKey
import '../services/websocket_service.dart';
import '../models/message.dart';

// ---- Providers defined first ----

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

final fileRequestProvider = StateNotifierProvider<FileRequestNotifier, List<Map<String, dynamic>>>((ref) {
  return FileRequestNotifier();
});

class FileRequestNotifier extends StateNotifier<List<Map<String, dynamic>>> {
  FileRequestNotifier() : super([]);

  void addRequest(Map<String, dynamic> req) {
    state = [...state, req];
  }

  void updateRequest(Map<String, dynamic> upd) {
    state = state.map((r) => (r['request_id'] == upd['request_id'] || r['id'] == upd['request_id']) ? {...r, ...upd} : r).toList();
  }

  void removeById(int id) {
    state = state.where((r) => (r['id'] ?? r['request_id']) != id).toList();
  }
}

// Now websocketProvider (uses the above)
final websocketProvider = Provider<WebSocketService>((ref) {
  final service = WebSocketService(
    onMessage: (msg) {
      ref.read(messageProvider.notifier).addMessage(msg);
    },
    onEvent: (event) {
      final eventType = event['event'] as String? ?? '';
      final data = event['data'] as Map<String, dynamic>? ?? {};
      if (eventType == 'file.access_request') {
        ref.read(fileRequestProvider.notifier).addRequest(data);
      } else if (eventType == 'file.access_request.updated') {
        ref.read(fileRequestProvider.notifier).updateRequest(data);
      } else if (eventType == 'task.started' || eventType == 'task.completed' || eventType == 'agent.assigned') {
        final msg = AIMessage(type: 'event', result: {'event': eventType, 'data': data});
        ref.read(messageProvider.notifier).addMessage(msg);
      }
    },
    onConnectionError: (error) {
      // Show snackbar using global scaffold messenger key
      final messenger = scaffoldMessengerKey.currentState;
      if (messenger != null) {
        messenger.showSnackBar(
          SnackBar(
            content: Text('WebSocket: $error'),
            backgroundColor: Colors.red,
            duration: const Duration(seconds: 4),
          ),
        );
      }
    },
  );
  service.connect();
  service.subscribe('file.access_request');
  service.subscribe('file.access_request.updated');
  service.subscribe('task.started');
  service.subscribe('task.completed');
  service.subscribe('agent.assigned');
  ref.onDispose(() => service.disconnect());
  return service;
});