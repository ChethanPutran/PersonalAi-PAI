import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/message.dart';
import '../services/notification_service.dart';

class WebSocketService {
  WebSocketChannel? _channel;

  final Function(AIMessage) onMessage;

  WebSocketService({
    required this.onMessage,
  });

  void connect() {
    try {
      final wsUrl =
          dotenv.env['WS_URL'] ??
              'ws://10.0.2.2:8000/ws';

      _channel = WebSocketChannel.connect(
        Uri.parse(wsUrl),
      );

      _channel!.stream.listen(
        (data) {
          try {
            final json =
                jsonDecode(data);

            if (json['type'] ==
                'notification.mobile') {
              NotificationService
                  .showNotification(
                json['data']['title'],
                json['data']['message'],
              );
            } else {
              onMessage(
                AIMessage.fromJson(json),
              );
            }
          } catch (e) {
            debugPrint(
              'Message parse error: $e',
            );
          }
        },
        onError: (error) {
          debugPrint(
            'WebSocket error: $error',
          );
        },
        onDone: () {
          debugPrint(
            'WebSocket disconnected',
          );
        },
      );
    } catch (e) {
      debugPrint(
        'Connection error: $e',
      );
    }
  }

  void sendGoal(
    String goal, {
    Map<String, dynamic>? context,
  }) {
    final message = AIMessage(
      type: 'goal',
      goal: goal,
      context: context,
    );

    _channel?.sink.add(
      jsonEncode(message.toJson()),
    );
  }

  void disconnect() {
    _channel?.sink.close();
  }
}