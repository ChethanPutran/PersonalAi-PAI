import 'package:flutter/material.dart';
import 'package:pai_mobile/auth/auth_service.dart';
import 'package:pai_mobile/chat/chat_status_service.dart';
import 'package:pai_mobile/models/chat.dart';
import 'package:pai_mobile/models/chat_status.dart';

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
  State<ChatScreen> createState() => _ChatScreenState();
}


class _ChatScreenState extends State<ChatScreen> {
  // Nullable because ChatStatusService requires a session id, which
  // doesn't exist until startNewSession() runs (or an existing one is
  // restored). Set asynchronously in _initStatusService.
  ChatStatusService? _statusService;
  AuthService? _authListener;   // holds the dispose handle

  final controller = TextEditingController();
  final scrollController = ScrollController();
  final List<ChatMessage> messages = [];
  List<ChatSessionSummary> sessions = [];
  bool loading = false;

  // -------------------------------------------------------------------
  // Lifecycle
  // -------------------------------------------------------------------

  @override
  void initState() {
    super.initState();
    _initStatusService();
    _loadSessionsAndCurrent();
  }

  @override
  void dispose() {
    _authListener?.removeListener(_onAuthChanged);
    _statusService?.dispose();
    scrollController.dispose();
    controller.dispose();
    super.dispose();
  }

/// Called whenever AuthService notifies (login, restore, refresh, 401).
/// Handles two cases:
///   • token became available → open the status WS
///   • token changed → dispose the old WS, open a new one with the
///     current token (WsClient.connect reads `api.authToken` at
///     connect time, so this is the right moment)
Future<void> _initStatusService() async {
  if (widget.service.sessionId == null) {
    await widget.service.startNewSession();
  }
  final sid = widget.service.sessionId;
  if (sid == null) return;

  _statusService = ChatStatusService(
    api: widget.service.api,
    sessionId: sid,
  );

  final auth = widget.service.api.auth;
  if (auth != null) {
    _authListener ??= auth;
    auth.addListener(_onAuthChanged);
  }

  if (auth == null || auth.token != null) {
    await _statusService!.connect();
  } else {
    debugPrint('[ChatScreen] token not ready; will connect on auth');
  }

  if (mounted) setState(() {});
}

void _onAuthChanged() {
  final auth = widget.service.api.auth;
  if (auth == null) return;

  if (auth.token == null) {
    // Logged out. Dispose the status WS — no token means every
    // reconnect attempt would 4401 loop.
    _statusService?.dispose();
    _statusService = null;
    if (mounted) setState(() {});
    return;
  }
  // Token just appeared or was refreshed. Open the WS (idempotent —
  // connect() cancels any prior sub before reconnecting).
  _statusService?.connect();
}

  /// Tear down the old WS, open a new one for the new session id.
  /// Called after every session change (switch or new chat).
  Future<void> _reconnectStatusService() async {
    _statusService?.dispose();
    _statusService = null;
    if (mounted) setState(() {});
    await _initStatusService();
  }

  // -------------------------------------------------------------------
  // Data loading
  // -------------------------------------------------------------------

  Future<void> _loadSessionsAndCurrent() async {
    try {
      sessions = await widget.service.listSessions();
      final current = widget.service.sessionId;
      if (!mounted) return;
      if (current != null) {
        final detail = await widget.service.loadSession(current);
        if (!mounted) return;
        setState(() => messages
          ..clear()
          ..addAll(detail.messages));
        _scrollToBottom();
      }
      if (mounted) setState(() {});
    } catch (e) {
      debugPrint('[ChatScreen] loadSessions failed: $e');
    }
  }

  Future<void> _openSession(ChatSessionSummary s) async {
    await widget.service.setSessionId(s.id);
    final detail = await widget.service.loadSession(s.id);
    if (!mounted) return;
    setState(() {
      messages
        ..clear()
        ..addAll(detail.messages);
    });
    await _reconnectStatusService();
    _scrollToBottom();
    if (mounted) Navigator.of(context).pop();
  }

  Future<void> _newChat() async {
    await widget.service.startNewSession();
    if (!mounted) return;
    setState(() => messages.clear());
    await _reconnectStatusService();
    if (mounted) Navigator.of(context).pop();
  }

  // -------------------------------------------------------------------
  // Send
  // -------------------------------------------------------------------

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
    _scrollToBottom();

    try {
      final res = await widget.service.sendMessage(
        deviceId: widget.deviceId,
        message: text,
      );

      // Clear the status bubble now that the reply has arrived. The
      // service also auto-clears after ~800ms if "done" arrives before
      // the POST returns; this is the deterministic path.
      _statusService?.clear();

      if (!mounted) return;
      setState(() {
        messages.add(ChatMessage(
          id: 'local-${DateTime.now().microsecondsSinceEpoch}',
          role: 'assistant',
          content: res.message,
          createdAt: DateTime.now(),
        ));
      });
      _scrollToBottom();

      // Refresh the session list so a new title/count shows up.
      try {
        final refreshed = await widget.service.listSessions();
        if (mounted) setState(() => sessions = refreshed);
      } catch (_) {}

      // First POST in a brand-new chat may have adopted a different
      // session id (if the client-generated one was overridden). If so,
      // move the status subscription to the new id.
      final newSid = widget.service.sessionId;
      if (newSid != null &&
          _statusService != null &&
          newSid != _statusService!.sessionId) {
        await _reconnectStatusService();
      }
    } catch (e) {
      _statusService?.clear();
      if (!mounted) return;
      setState(() {
        messages.add(ChatMessage(
          id: 'err-${DateTime.now().microsecondsSinceEpoch}',
          role: 'assistant',
          content: 'Error: $e',
          createdAt: DateTime.now(),
        ));
      });
      _scrollToBottom();
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  // -------------------------------------------------------------------
  // Scroll
  // -------------------------------------------------------------------

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!scrollController.hasClients) return;
      scrollController.animateTo(
        scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    });
  }

  // -------------------------------------------------------------------
  // Build
  // -------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('PAI')),
      drawer: _buildDrawer(context),
      body: Column(
        children: [
          Expanded(child: _buildMessageList(context)),
          _buildInputRow(context),
        ],
      ),
    );
  }

  Widget _buildDrawer(BuildContext context) {
    // The old drawer had `itemCount: messages.length + ...` but read
    // `sessions[i]` — a mismatch that threw on first open. This shows
    // sessions, which is what a chat drawer should list.
    return Drawer(
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
              child: sessions.isEmpty
                  ? const Center(
                      child: Padding(
                        padding: EdgeInsets.all(24),
                        child: Text(
                          'No chats yet.\nStart a conversation.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: Colors.grey),
                        ),
                      ),
                    )
                  : ListView.builder(
                      itemCount: sessions.length,
                      itemBuilder: (_, index) {
                        final s = sessions[index];
                        return ListTile(
                          title: Text(
                            s.title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                          subtitle: Text('${s.messageCount} messages'),
                          selected: s.id == widget.service.sessionId,
                          onTap: () => _openSession(s),
                          trailing: IconButton(
                            icon: const Icon(Icons.delete_outline),
                            onPressed: () async {
                              await widget.service.deleteSession(s.id);
                              if (widget.service.sessionId == s.id) {
                                await widget.service.startNewSession();
                                if (!mounted) return;
                                setState(() => messages.clear());
                                await _reconnectStatusService();
                              }
                              try {
                                final refreshed =
                                    await widget.service.listSessions();
                                if (mounted) {
                                  setState(() => sessions = refreshed);
                                }
                              } catch (_) {}
                            },
                          ),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildMessageList(BuildContext context) {
    // Always reserve one slot at the bottom for the status bubble.
    // When there's no live status, the builder returns a zero-height
    // SizedBox — the slot is invisible but avoids rebuilding the
    // ListView's itemCount on every status change.
    return ListView.builder(
      controller: scrollController,
      itemCount: messages.length + 1,
      itemBuilder: (_, i) {
        if (i == messages.length) {
          return _buildStatusBubble();
        }
        return _buildMessageBubble(context, messages[i]);
      },
    );
  }

  Widget _buildMessageBubble(BuildContext context, ChatMessage m) {
    final isUser = m.role == 'user';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
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
  }

  Widget _buildStatusBubble() {
    final svc = _statusService;
    if (svc == null) return const SizedBox.shrink();

    // Rebuilds every time `svc` notifies — i.e., when a new status
    // event arrives or the send completes and calls clear().
    return ListenableBuilder(
      listenable: svc,
      builder: (_, __) {
        final status = svc.current;
        // Hide null and terminal statuses. Terminal ("done" / "failed")
        // is redundant with the assistant message / error that replaces
        // it. Rendering them would show a "Done" flash after the reply
        // already appeared.
        if (status == null || status.isTerminal) {
          return const SizedBox.shrink();
        }
        return _StatusBubble(status: status);
      },
    );
  }

  Widget _buildInputRow(BuildContext context) {
    // `loading` is the only gate — the status service is informational.
    // Blocking send on `statusService.isWorking` would race: the first
    // "planning" event arrives *after* the POST starts.
    return SafeArea(
      top: false,
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              decoration: const InputDecoration(hintText: 'Ask PAI...'),
              onSubmitted: (_) => _send(),
              enabled: !loading,
            ),
          ),
          IconButton(
            onPressed: loading ? null : _send,
            icon: const Icon(Icons.send),
          ),
        ],
      ),
    );
  }
}


// =====================================================================
// Status bubble
// =====================================================================

class _StatusBubble extends StatelessWidget {
  final ChatStatus status;
  const _StatusBubble({required this.status});

  @override
  Widget build(BuildContext context) {
    final color = status.isFailure
        ? Colors.red.shade400
        : Theme.of(context).colorScheme.primary;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: 14,
            height: 14,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              valueColor: AlwaysStoppedAnimation(color),
            ),
          ),
          const SizedBox(width: 8),
          Flexible(
            child: Text(
              status.message,
              style: TextStyle(
                fontSize: 13,
                color: color,
                fontStyle: FontStyle.italic,
              ),
            ),
          ),
        ],
      ),
    );
  }
}