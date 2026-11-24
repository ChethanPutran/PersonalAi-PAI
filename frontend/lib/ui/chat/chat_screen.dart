
import 'package:flutter/material.dart';
import 'package:pai_mobile/auth/auth_service.dart';
import 'package:pai_mobile/chat/chat_status_service.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/models/chat.dart';
import 'package:pai_mobile/tasks/confirmation_service.dart';
import 'package:pai_mobile/ui/plugins/permissions/confirmation_dialog.dart';

import 'package:uuid/uuid.dart';

import '../../chat/chat_service.dart';
import '../../chat/chat_stream.dart';
import '../shell/pai_shell.dart' show PaiBrandMark;
import '../theme/theme.dart';
import '../voice/voice_screen.dart';
import '../widgets/hud/hud.dart';
import '../widgets/markdown/markdown.dart';


class ChatScreen extends StatefulWidget {
  final ChatService service;
  final String deviceId;

  const ChatScreen({
    super.key,
    required this.service,
    required this.deviceId,
    this.statusServiceFactory,
  });

  /// Builds the live-status service for a session. Defaults to the real WebSocket-backed one; tests inject a fake.
  final ChatStatusService Function(ApiClient api, String sessionId)? statusServiceFactory;

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
  final svc = _confirmationService;
  if (svc == null || !mounted) return;
  // The same approval dialog the plugin tester uses: what will run, how risky it is, and
  // (when the server allows) "once / this session / always" with parameter binding.
  await showConfirmationDialog(
    context,
    request: p.request,
    onConfirm: svc.confirmWith,
    onDeny: svc.deny,
  );
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

  await _attachLiveServices(sid);
}

/// (Re)creates the live-status and confirmation services for [sid] and connects them. Does NOT check that the
/// session exists on the server: a brand-new chat attaches them BEFORE its first message creates the session.
Future<void> _attachLiveServices(String sid) async {
  // Clean up old instances
  _statusService?.dispose();
  _confirmationService?.removeListener(_onConfirmationChanged);
  _confirmationService?.dispose();


  // Initialize fresh services with the active session ID
  _statusService = widget.statusServiceFactory?.call(widget.service.api, sid) ??
      ChatStatusService(
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
        createdAt: PaiClock.now(),
      ));
      loading = true;
    });
    _scrollToBottom();

    try {
      // A new chat has no session yet. Pick its id now and open the live-status (and confirmation) sockets
      // BEFORE the first POST, so the first message gets the same stage-by-stage status as every later one.
      // The server only forwards events to sockets that are already subscribed, hence the (bounded) wait.
      if (widget.service.sessionId == null) {
        final sid = widget.service.beginClientSession();
        await _attachLiveServices(sid);
        await _statusService?.waitUntilConnected();
      }

      // The reply streams in token-by-token; `_runTurn` falls back to the
      // plain POST /chat if streaming is unavailable.
      await _runTurn(text);

      // Clear the status bubble now that the reply has arrived. The
      // service also auto-clears after ~800ms if "done" arrives before
      // the POST returns; this is the deterministic path.
      _statusService?.clear();

      if (!mounted) return;
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
          createdAt: PaiClock.now(),
        ));
      });
      _scrollToBottom();
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  /// Sends [text] and renders the assistant reply as it streams in.
  ///
  /// Falls back to the non-streaming POST only when the stream failed before
  /// producing anything; both calls carry the same Idempotency-Key, so the goal
  /// can never run twice. If the stream breaks mid-reply, the partial text stays
  /// and an error note is appended (re-sending would duplicate the turn).
  Future<void> _runTurn(String text) async {
    final key = const Uuid().v4();
    final msgId = 'local-${DateTime.now().microsecondsSinceEpoch}';
    final buf = StringBuffer();
    var index = -1;
    var gotAny = false;

    void showAssistant(String content, {String? id}) {
      if (!mounted) return;
      final msg = ChatMessage(
        id: id ?? msgId,
        role: 'assistant',
        content: content,
        createdAt: PaiClock.now(),
      );
      setState(() {
        if (index < 0) {
          messages.add(msg);
          index = messages.length - 1;
        } else {
          messages[index] = msg;
        }
      });
      _scrollToBottom();
    }

    try {
      await for (final ev in widget.service.sendMessageStream(
        deviceId: widget.deviceId,
        message: text,
        idempotencyKey: key,
      )) {
        switch (ev) {
          case ChatStreamStatus():
            break; // the status WS already renders progress
          case ChatStreamDelta():
            gotAny = true;
            buf.write(ev.delta);
            showAssistant(buf.toString());
          case ChatStreamDone():
            gotAny = true;
            showAssistant(ev.response.message); // authoritative final text
          case ChatStreamError():
            throw StateError(ev.message);
        }
      }
      if (!gotAny) throw StateError('Empty stream');
    } catch (e) {
      if (gotAny) {
        showAssistant('${buf.toString()}\n\n[Connection interrupted: $e]');
        return;
      }
      debugPrint('[ChatScreen] streaming unavailable ($e); falling back to POST');
      final res = await widget.service.sendMessage(
        deviceId: widget.deviceId,
        message: text,
        idempotencyKey: key,
      );
      showAssistant(res.message);
    }
  }

  Future<void> _openVoice() async {
    await VoiceScreen.open(
      context,
      chat: widget.service,
      deviceId: widget.deviceId,
    );
    // Voice turns were stored in this chat session: pull them in.
    if (!mounted) return;
    await _loadSessionsAndCurrent();
    await _reconnectStatusService();
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
    return HudScaffold(
      appBar: HudAppBar(
        title: 'Assistant',
        actions: [
          HudIconButton(
            tooltip: 'Voice conversation',
            icon: Icons.graphic_eq,
            active: true,
            onPressed: _openVoice,
          ),
          const SizedBox(width: 8),
        ],
      ),
      drawer: _buildDrawer(context),
      body: Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 860),
          child: Column(
            children: [
              Expanded(child: _buildMessageList(context)),
              _buildInputRow(context),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildDrawer(BuildContext context) {
    // The old drawer had `itemCount: messages.length + ...` but read
    // `sessions[i]` — a mismatch that threw on first open. This shows
    // sessions, which is what a chat drawer should list.
    return Drawer(
      backgroundColor: PaiColors.bg1,
      shape: const RoundedRectangleBorder(),
      child: SafeArea(
        child: Column(
          children: [
            const SizedBox(height: PaiSpacing.lg),
            const PaiBrandMark(),
            const SizedBox(height: PaiSpacing.lg),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg),
              child: GlowButton(
                label: 'New chat',
                icon: Icons.add,
                expanded: true,
                variant: GlowVariant.outline,
                onPressed: _newChat,
              ),
            ),
            const HudSectionHeader('Conversations'),
            Expanded(
              child: sessions.isEmpty
                  ? const HudEmptyState(
                      icon: Icons.forum_outlined,
                      title: 'No chats yet',
                      message: 'Start a conversation.',
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

  static const _suggestions = [
    'What can you do?',
    'Take a photo',
    'Remind me to call Mom at 6pm',
    'Show my recent tasks',
  ];

  Widget _buildMessageList(BuildContext context) {
    if (messages.isEmpty && !loading) {
      return _buildEmpty(context);
    }
    // Always reserve one slot at the bottom for the status bubble.
    // When there's no live status, the builder returns a zero-height
    // SizedBox — the slot is invisible but avoids rebuilding the
    // ListView's itemCount on every status change.
    return ListView.builder(
      controller: scrollController,
      padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg, vertical: PaiSpacing.md),
      itemCount: messages.length + 1,
      itemBuilder: (_, i) {
        if (i == messages.length) {
          return _buildStatusBubble();
        }
        final m = messages[i];
        final isLast = i == messages.length - 1;
        return ChatMessageBubble(
          message: m,
          // The assistant message at the end of the list is still arriving while a turn is running.
          streaming: loading && isLast && m.role != 'user',
        );
      },
    );
  }

  Widget _buildEmpty(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(PaiSpacing.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const ArcReactorOrb(state: OrbState.idle, size: 132),
            const SizedBox(height: PaiSpacing.lg),
            Text('HOW CAN I HELP?',
                style: Theme.of(context).textTheme.titleLarge?.copyWith(color: scheme.primary, letterSpacing: 4)),
            const SizedBox(height: PaiSpacing.xs),
            Text('Type a request, or tap the waveform to talk.',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: scheme.onSurfaceVariant)),
            const SizedBox(height: PaiSpacing.xl),
            Wrap(
              alignment: WrapAlignment.center,
              spacing: PaiSpacing.sm,
              runSpacing: PaiSpacing.sm,
              children: [
                for (final text in _suggestions)
                  ActionChip(
                    label: Text(text),
                    avatar: Icon(Icons.north_east, size: 14, color: scheme.primary),
                    onPressed: () => setState(() => controller.text = text),
                  ),
              ],
            ),
          ],
        ),
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
      // Once the assistant's text is arriving, its blinking cursor says "working".
      if (messages.isNotEmpty && messages.last.role != 'user') return const SizedBox.shrink();
      return const _StatusLine(message: 'Working…');
    }

    // WS is live — show real status events.
    return ListenableBuilder(
      listenable: svc,
      builder: (_, _) {
        final status = svc.current;
        if (status == null || status.isTerminal) {
          // The request is out but the first stage has not arrived yet: say so instead of showing nothing.
          final waitingForFirstStage = status == null &&
              loading &&
              (messages.isEmpty || messages.last.role == 'user');
          return waitingForFirstStage ? const _StatusLine(message: 'Working…') : const SizedBox.shrink();
        }
        return _StatusLine(message: status.message, failure: status.isFailure);
      },
    );
  }

  Widget _buildInputRow(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    // `loading` is the only gate — the status service is informational.
    // Blocking send on `statusService.isWorking` would race: the first
    // "planning" event arrives *after* the POST starts.
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, PaiSpacing.sm, PaiSpacing.sm, PaiSpacing.md),
        decoration: BoxDecoration(
          border: Border(top: BorderSide(color: PaiColors.border.withValues(alpha: 0.5))),
        ),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: controller,
                style: Theme.of(context).textTheme.bodyLarge,
                decoration: InputDecoration(
                  hintText: loading ? 'PAI is responding…' : 'Ask PAI…',
                  prefixIcon: Icon(Icons.chevron_right, color: scheme.primary),
                ),
                onSubmitted: (_) => _send(),
                enabled: !loading,
              ),
            ),
            const SizedBox(width: PaiSpacing.sm),
            HudIconButton(
              tooltip: 'Send',
              icon: Icons.arrow_upward_rounded,
              filled: true,
              active: true,
              onPressed: loading ? null : _send,
            ),
          ],
        ),
      ),
    );
  }
}

// =====================================================================
// Message bubble
// =====================================================================

/// One chat message. Public so widget tests can render it without the whole screen.
@visibleForTesting
class ChatMessageBubble extends StatelessWidget {
  final ChatMessage message;
  final bool streaming;
  const ChatMessageBubble({super.key, required this.message, required this.streaming});

  static String _time(DateTime t) {
    final l = t.toLocal();
    return '${l.hour.toString().padLeft(2, '0')}:${l.minute.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final isUser = message.role == 'user';
    final accent = isUser ? PaiColors.teal : scheme.primary;
    final maxW = (MediaQuery.sizeOf(context).width * 0.82).clamp(0.0, 640.0);

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: PaiSpacing.sm),
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: maxW),
          child: Semantics(
            container: true,
            label: '${isUser ? 'You' : 'Assistant'} said',
            child: Column(
              crossAxisAlignment: isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.only(bottom: 4, left: 2, right: 2),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(isUser ? 'YOU' : 'PAI',
                          style: text.labelSmall?.copyWith(color: accent, letterSpacing: 2.5)),
                      const SizedBox(width: 8),
                      Text(_time(message.createdAt), style: context.pai.mono(size: 11, faint: true)),
                    ],
                  ),
                ),
                DecoratedBox(
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.only(
                      topLeft: const Radius.circular(PaiRadii.md),
                      topRight: const Radius.circular(PaiRadii.md),
                      bottomLeft: Radius.circular(isUser ? PaiRadii.md : 2),
                      bottomRight: Radius.circular(isUser ? 2 : PaiRadii.md),
                    ),
                    color: isUser
                        ? scheme.secondaryContainer.withValues(alpha: 0.55)
                        // Theme-aware: markdown text/code colours come from the palette and must
                        // sit on a matching surface (a fixed dark bubble made light mode unreadable).
                        : (context.pai.dark ? PaiColors.bg2.withValues(alpha: 0.85) : PaiColors.lightSurface),
                    border: Border.all(color: accent.withValues(alpha: isUser ? 0.35 : 0.28)),
                    boxShadow: streaming
                        ? [BoxShadow(color: accent.withValues(alpha: 0.18), blurRadius: 16, spreadRadius: -4)]
                        : null,
                  ),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
                    child: isUser
                        // What the user typed is shown verbatim (never interpreted as markdown).
                        ? SelectableText(message.content, style: text.bodyLarge)
                        : Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              MarkdownMessage(data: message.content, streaming: streaming),
                              if (streaming) const Padding(
                                padding: EdgeInsets.only(top: 4),
                                child: BlinkingCursor(),
                              ),
                            ],
                          ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// =====================================================================
// Status line ("assistant is working")
// =====================================================================

class _StatusLine extends StatelessWidget {
  final String message;
  final bool failure;
  const _StatusLine({required this.message, this.failure = false});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = failure ? scheme.error : scheme.primary;
    return Padding(
      padding: const EdgeInsets.fromLTRB(2, PaiSpacing.xs, 2, PaiSpacing.md),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          failure ? Icon(Icons.error_outline, size: 16, color: color) : ThinkingDots(color: color),
          const SizedBox(width: 10),
          Flexible(
            child: Text(
              message.toUpperCase(),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.labelSmall?.copyWith(color: color, letterSpacing: 1.8),
            ),
          ),
        ],
      ),
    );
  }
}
