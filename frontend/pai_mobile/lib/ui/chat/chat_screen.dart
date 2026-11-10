import 'package:flutter/material.dart';
import 'package:pai_mobile/models/chat.dart';

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
  final List<ChatMessage> messages = [];
  List<ChatSessionSummary> sessions = [];
  bool loading = false;

   @override
  void initState() {
    super.initState();
    _loadSessionsAndCurrent();
  }

  Future<void> _loadSessionsAndCurrent() async {
    sessions = await widget.service.listSessions();
    final current = widget.service.sessionId;
    if (!mounted) return;
    if (current != null) {
      final detail = await widget.service.loadSession(current);
      setState(() => messages..clear()..addAll(detail.messages));
    }
    setState(() {});
  }

  Future<void> _openSession(ChatSessionSummary s) async {
    await widget.service.setSessionId(s.id);
    final detail = await widget.service.loadSession(s.id);
    if (!mounted) return;
    setState(() {
      messages..clear()..addAll(detail.messages);
    });
    Navigator.of(context).pop();   // close drawer
  }

  void _newChat() {
    widget.service.startNewSession();
    if (!mounted) return;
    setState(() => messages.clear());
    Navigator.of(context).pop();
  }

  Future<void> _send() async {
    final text = controller.text.trim();
    if (text.isEmpty || loading) return;
    controller.clear();
    if (!mounted) return;
    setState(() {
      messages.add(ChatMessage(
        id: 'local-${DateTime.now().microsecondsSinceEpoch}',
        role: 'user',
        content: text,
        createdAt: DateTime.now(),
      ));
      loading = true;
    });

    try {
      final res = await widget.service.sendMessage(
        deviceId: widget.deviceId, message: text,
      );
      if (!mounted) return;
      setState(() {
        messages.add(ChatMessage(
          id: 'local-${DateTime.now().microsecondsSinceEpoch}',
          role: 'assistant',
          content: res.message,
          createdAt: DateTime.now(),
        ));
      });
      // refresh session list so the new title/count shows up
      sessions = await widget.service.listSessions();
      if (mounted) setState(() {});
    } catch (e) {
      if (!mounted) return;
      setState(() {
        messages.add(ChatMessage(
          id: 'err-${DateTime.now().microsecondsSinceEpoch}',
          role: 'assistant',
          content: 'Error: $e',
          createdAt: DateTime.now(),
        ));
      });
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }



@override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('PAI')),
      drawer: Drawer(
        child: SafeArea(
          child: Column(
            children: [
              ListTile(
                leading: const Icon(Icons.add),
                title: const Text('New chat'),
                onTap: _newChat,
              ),
              const Divider(height: 1),
              Expanded(
                child: ListView.builder(
                  itemCount: sessions.length,
                  itemBuilder: (_, i) {
                    final s = sessions[i];
                    return ListTile(
                      title: Text(s.title, maxLines: 1,
                          overflow: TextOverflow.ellipsis),
                      subtitle: Text('${s.messageCount} messages'),
                      selected: s.id == widget.service.sessionId,
                      onTap: () => _openSession(s),
                      trailing: IconButton(
                        icon: const Icon(Icons.delete_outline),
                        onPressed: () async {
                        await widget.service.deleteSession(s.id);
                        if (widget.service.sessionId == s.id) {
                          await widget.service.startNewSession();
                          if (mounted) setState(() => messages.clear());
                        }
                        final refreshed = await widget.service.listSessions();
                        if (mounted) setState(() => sessions = refreshed);
                      },
                      ),
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              itemCount: messages.length,
              itemBuilder: (_, i) {
                final m = messages[i];
                final isUser = m.role == 'user';
                return Align(
                  alignment: isUser
                      ? Alignment.centerRight : Alignment.centerLeft,
                  child: Container(
                    margin: const EdgeInsets.all(8),
                    padding: const EdgeInsets.all(12),
                    constraints: BoxConstraints(
                      maxWidth: MediaQuery.of(context).size.width * 0.75,
                    ),
                    decoration: BoxDecoration(
                      color: isUser
                          ? Theme.of(context).colorScheme.primaryContainer
                          : Theme.of(context).colorScheme.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(m.content),
                  ),
                );
              },
            ),
          ),
          Row(children: [
            Expanded(
              child: TextField(
                controller: controller,
                decoration: const InputDecoration(hintText: 'Ask PAI...'),
                onSubmitted: (_) => _send(),
              ),
            ),
            IconButton(
              onPressed: loading ? null : _send,
              icon: const Icon(Icons.send),
            ),
          ]),
        ],
      ),
    );
  }
}



