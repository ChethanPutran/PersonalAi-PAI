import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/websocket_provider.dart';
import '../services/voice_service.dart';
import '../services/camera_service.dart';

class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  final TextEditingController _textController = TextEditingController();
  final VoiceService _voice = VoiceService();
  bool _isVoiceListening = false;
  final ScrollController _scrollController = ScrollController();

  @override
  void initState() {
    super.initState();
    _voice.initialize();
  }

  void _sendGoal(String goal) {
    if (goal.trim().isEmpty) return;
    final ws = ref.read(websocketProvider);
    ws.sendGoal(goal, context: {'device': 'mobile', 'timestamp': DateTime.now().toIso8601String()});
    _textController.clear();
  }

  void _sendVoiceGoal() async {
    if (_isVoiceListening) {
      _voice.stopListening();
      setState(() => _isVoiceListening = false);
      return;
    }
    setState(() => _isVoiceListening = true);
    _voice.startListening((recognized) {
      _textController.text = recognized;
      _sendGoal(recognized);
      setState(() => _isVoiceListening = false);
      _voice.stopListening();
    });
  }
  Future<void> _captureAndSend() async {
  final image = await CameraService.captureImage();
  if (image != null) {
    // Read image as base64
    final bytes = await image.readAsBytes();
    final base64Image = base64Encode(bytes);
    
    // Send via WebSocket as a vision goal
    final ws = ref.read(websocketProvider);
    ws.sendGoal("Analyze this image", context: {
      'image_data': base64Image,
      'task': 'describe_scene'  // or 'detect_objects', 'ocr'
    });
  }
}

  @override
  Widget build(BuildContext context) {
    final messages = ref.watch(messageProvider);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });

    return Scaffold(
      appBar: AppBar(
        title: const Text('Personal AI Assistant'),
        actions: [
          IconButton(
            icon: const Icon(Icons.camera_alt),
            onPressed: _captureAndSend,
          ),
          IconButton(
            icon: Icon(_isVoiceListening ? Icons.mic : Icons.mic_none),
            onPressed: _sendVoiceGoal,
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              controller: _scrollController,
              itemCount: messages.length,
              itemBuilder: (ctx, i) {
                final msg = messages[i];
                if (msg.goal != null) {
                  return _buildUserBubble(msg.goal!);
                } else if (msg.result != null) {
                  return _buildAIBubble(msg.result!);
                }
                return const SizedBox();
              },
            ),
          ),
          _buildInputArea(),
        ],
      ),
    );
  }

  Widget _buildUserBubble(String text) {
    return Align(
      alignment: Alignment.centerRight,
      child: Container(
        margin: const EdgeInsets.all(8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: Colors.blueAccent,
          borderRadius: BorderRadius.circular(16),
        ),
        child: Text(text, style: const TextStyle(color: Colors.white)),
      ),
    );
  }

  Widget _buildAIBubble(Map<String, dynamic> result) {
    String text = result['results']?.toString() ?? result['summary']?.toString() ?? 'Done';
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.all(8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: Colors.grey[800],
          borderRadius: BorderRadius.circular(16),
        ),
        child: Text(text, style: const TextStyle(color: Colors.white)),
      ),
    );
  }

  Widget _buildInputArea() {
    return Container(
      padding: const EdgeInsets.all(8),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _textController,
              decoration: const InputDecoration(
                hintText: 'Type or speak...',
                border: OutlineInputBorder(),
              ),
              onSubmitted: (_) => _sendGoal(_textController.text),
            ),
          ),
          const SizedBox(width: 8),
          IconButton(
            icon: const Icon(Icons.send),
            onPressed: () => _sendGoal(_textController.text),
          ),
        ],
      ),
    );
  }
}