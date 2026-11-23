import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:pai_mobile/auth/auth_service.dart';
import 'package:pai_mobile/chat/chat_status_service.dart';
import 'package:pai_mobile/models/chat.dart';
import 'package:pai_mobile/tasks/confirmation_service.dart';

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
  ConfirmationService? _confirmationService;
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
  // Load (and validate) the session before opening a WS to it.
  // If the stored id is stale, _loadSessionsAndCurrent clears it and
  // _initStatusService becomes a no-op.
  _bootstrap();
}
Future<void> _bootstrap() async {
  await _loadSessionsAndCurrent();
  await _initStatusService();
}

void _onConfirmationChanged() {
  final pending = _confirmationService?.pending;
  if (pending == null) return;
  _showConfirmationDialog(pending);
}

Future<void> _showConfirmationDialog(PendingConfirmation p) async {
  final params = const JsonEncoder.withIndent('  ').convert(p.parameters);
  final allow = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (_) => AlertDialog(
      title: Text('Approve ${p.capability}?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (p.reason.isNotEmpty) Text(p.reason),
          const SizedBox(height: 8),
          const Text('Parameters:', style: TextStyle(fontWeight: FontWeight.w600)),
          SelectableText(params, style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('Deny'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('Approve'),
        ),
      ],
    ),
  );
  if (allow == true) {
    await _confirmationService?.confirm();
  } else {
    await _confirmationService?.deny();
  }
}

 @override
  void dispose() {
    _authListener?.removeListener(_onAuthChanged);
    _statusService?.dispose();
    _confirmationService?.removeListener(_onConfirmationChanged);
    _confirmationService?.dispose();
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
  final sid = widget.service.sessionId;

  // No id → nothing to subscribe to. The first sendMessage() will
  // create one, and _send() will reconnect the status service.
  if (sid == null) return;

  // Verify the session actually exists on the server before opening
  // a WS to it. This is what kills the 403 storm after a stale id
  // survives an app restart, and it fixes the 404 on load.
  try {
    await widget.service.loadSession(sid);
  } catch (e) {
    debugPrint('[ChatScreen] session $sid not on server, clearing: $e');
    await widget.service.clearSession();
    return;
  }

  // Clean up old instances
  _statusService?.dispose();
  _confirmationService?.removeListener(_onConfirmationChanged);
  _confirmationService?.dispose();


  // Initialize fresh services with the active session ID
  _statusService = ChatStatusService(
    api: widget.service.api,
    sessionId: sid,
  );

  _confirmationService = ConfirmationService(
    api: widget.service.api,
    sessionId: sid,
  );
  _confirmationService!.addListener(_onConfirmationChanged);

  final auth = widget.service.api.auth;
  if (auth != null) {
    // Register ONCE. The old code called addListener on every
    // reconnect, so N reconnects → N listeners → N sockets.
    if (_authListener == null) {
      _authListener = auth;
      auth.addListener(_onAuthChanged);
    }
  }

  if (auth == null || auth.token != null) {
    await _statusService!.connect();
    await _confirmationService!.connect();
  } else {
    debugPrint('[ChatScreen] token not ready; will connect on auth');
  }

  if (mounted) setState(() {});
}

void _onAuthChanged() {
  final auth = widget.service.api.auth;
  if (auth == null) return;

  if (auth.token == null) {
    _statusService?.dispose();
    _statusService = null;
    _confirmationService?.removeListener(_onConfirmationChanged);
    _confirmationService?.dispose();
    _confirmationService = null;
    if (mounted) setState(() {});
    return;
  }
  _statusService?.connect();
  _confirmationService?.connect();
}

  /// Tear down the old WS, open a new one for the new session id.
  /// Called after every session change (switch or new chat).
  Future<void> _reconnectStatusService() async {
    _statusService?.dispose();
    _statusService = null;
    _confirmationService?.removeListener(_onConfirmationChanged);
    _confirmationService?.dispose();
    _confirmationService = null;
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
    // Server says the session is gone — don't keep retrying it.
    final msg = e.toString();
    if (msg.contains('404') || msg.contains('Session not found')) {
      await widget.service.clearSession();
    }
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
      // Reconnect if:
      //   • we don't have a service yet (first message in a new chat), OR
      //   • the session id changed (server adopted a different id).
      final currentStatusService = _statusService;
      if (newSid != null &&
          (currentStatusService == null || newSid != currentStatusService.sessionId)) {
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

  // No WS yet (first message in a new chat) — use the POST loading
  // flag as a coarse "Working…" indicator. Coarser than the WS's
  // stage-by-stage updates, but better than nothing.
  if (svc == null) {
    if (!loading) return const SizedBox.shrink();
    final color = Theme.of(context).colorScheme.primary;
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
              'Working…',
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

  // WS is live — show real status events.
  return ListenableBuilder(
    listenable: svc,
    builder: (_, _) {
      final status = svc.current;
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
