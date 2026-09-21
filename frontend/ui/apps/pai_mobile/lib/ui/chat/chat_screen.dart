import 'package:flutter/material.dart';

import '../../chat/chat_service.dart';


class ChatScreen extends StatefulWidget {
  final ChatService service;
  final String deviceId;

  const ChatScreen({
    super.key,
    required this.service,
    required this.deviceId,
  });


  @override
  State<ChatScreen> createState() =>
      _ChatScreenState();
}


class _ChatScreenState
    extends State<ChatScreen> {

  final controller = TextEditingController();

  final List<String> messages = [];

  bool loading = false;


  Future<void> send() async {
    final text = controller.text.trim();

    if (text.isEmpty || loading) {
      return;
    }

    controller.clear();

    setState(() {
      messages.add('You: $text');
      loading = true;
    });

    try {
      final response =
          await widget.service.sendMessage(
        deviceId: widget.deviceId,
        message: text,
      );

      setState(() {
        messages.add('PAI: $response');
      });
    } catch (e) {
      setState(() {
        messages.add('Error: $e');
      });
    } finally {
      setState(() {
        loading = false;
      });
    }
  }


  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('PAI'),
      ),

      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              itemCount: messages.length,

              itemBuilder: (_, index) {
                return Padding(
                  padding: const EdgeInsets.all(12),
                  child: Text(messages[index]),
                );
              },
            ),
          ),

          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: controller,
                  decoration: const InputDecoration(
                    hintText: 'Ask PAI...',
                  ),
                  onSubmitted: (_) => send(),
                ),
              ),

              IconButton(
                onPressed: loading ? null : send,
                icon: const Icon(Icons.send),
              ),

              IconButton(
                onPressed: () {
                  // Voice mode will be connected here.
                },
                icon: const Icon(Icons.mic),
              ),
            ],
          ),
        ],
      ),
    );
  }
}