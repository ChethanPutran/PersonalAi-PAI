import 'package:flutter/material.dart';

import '../../chat/chat_service.dart';
import '../../services/voice/voice_controller.dart';
import '../../services/voice/voice_events.dart';
import '../../services/voice/voice_socket.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';
import '../widgets/markdown/markdown_safety.dart' show stripMarkdown;

/// Full-screen, hands-free voice conversation (ChatGPT-voice style).
///
/// Push it with [VoiceScreen.open]; it returns when the user ends the call so the
/// caller can refresh the chat (voice turns are stored in the same session).
class VoiceScreen extends StatefulWidget {
  final VoiceController? controller; // injectable for tests
  final ChatService? chat;
  final String deviceId;

  const VoiceScreen({
    super.key,
    this.controller,
    this.chat,
    required this.deviceId,
  }) : assert(controller != null || chat != null,
            'pass a controller, or a ChatService to build one');

  static Future<void> open(
    BuildContext context, {
    required ChatService chat,
    required String deviceId,
  }) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute(
        fullscreenDialog: true,
        builder: (_) => VoiceScreen(chat: chat, deviceId: deviceId),
      ),
    );
  }

  @override
  State<VoiceScreen> createState() => _VoiceScreenState();
}

class _VoiceScreenState extends State<VoiceScreen> {
  late final VoiceController _c;
  final _typed = TextEditingController();
  bool _showKeyboard = false;

  @override
  void initState() {
    super.initState();
    _c = widget.controller ??
        VoiceController.create(
          api: widget.chat!.api,
          chat: widget.chat!,
          deviceId: widget.deviceId,
        );
    _c.start();
  }

  @override
  void dispose() {
    _typed.dispose();
    _c.dispose();
    super.dispose();
  }

  Future<void> _end() async {
    await _c.stop();
    if (mounted) Navigator.of(context).pop();
  }

  String get _label {
    if (_c.error != null && !_c.active) return 'Not connected';
    switch (_c.connection) {
      case VoiceConnection.connecting:
        return 'Connecting…';
      case VoiceConnection.reconnecting:
        return 'Reconnecting…';
      default:
    }
    if (_c.muted) return 'Muted';
    if (!_c.ready) return 'Getting ready…';
    return switch (_c.state) {
      VoiceState.idle => 'Listening',
      VoiceState.listening => 'Listening…',
      VoiceState.thinking =>
        _c.statusMessage.isNotEmpty ? _c.statusMessage : 'Thinking…',
      VoiceState.speaking => 'Speaking',
    };
  }

  OrbState get _orbState {
    if (_c.muted || !_c.active) return OrbState.idle;
    return switch (_c.state) {
      VoiceState.idle => OrbState.listening, // hands-free: the mic is open between turns
      VoiceState.listening => OrbState.listening,
      VoiceState.thinking => OrbState.thinking,
      VoiceState.speaking => OrbState.speaking,
    };
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: _c,
      builder: (context, _) {
        final speaking = _c.state == VoiceState.speaking;
        final orb = _orbState;
        final shortest = MediaQuery.sizeOf(context).shortestSide;
        final orbSize = (shortest * 0.72).clamp(200.0, 340.0);
        final labelColor = _c.error != null && !_c.active
            ? scheme.error
            : _c.muted
                ? PaiColors.amber
                : orb.color;
        return HudScaffold(
          body: SafeArea(
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 640),
                child: Column(
                  children: [
                    Padding(
                      padding: const EdgeInsets.fromLTRB(8, 4, 8, 0),
                      child: Row(
                        children: [
                          HudIconButton(
                            tooltip: 'Close',
                            icon: Icons.keyboard_arrow_down,
                            onPressed: _end,
                          ),
                          const Spacer(),
                          Text('VOICE LINK',
                              style: Theme.of(context).textTheme.labelMedium?.copyWith(
                                  color: scheme.onSurfaceVariant, letterSpacing: 4)),
                          const Spacer(),
                          HudIconButton(
                            tooltip: 'Type instead',
                            icon: _showKeyboard
                                ? Icons.keyboard_hide_outlined
                                : Icons.keyboard_outlined,
                            active: _showKeyboard,
                            onPressed: () => setState(() => _showKeyboard = !_showKeyboard),
                          ),
                        ],
                      ),
                    ),
                    Expanded(
                      flex: 5,
                      child: Center(
                        child: GestureDetector(
                          behavior: HitTestBehavior.opaque,
                          onTap: speaking ? _c.interrupt : null,
                          child: ArcReactorOrb(
                            state: orb,
                            level: _c.muted || !_c.active ? 0 : _c.level,
                            size: orbSize,
                          ),
                        ),
                      ),
                    ),
                    Text(_label,
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontFamily: PaiFonts.display,
                          fontSize: 17,
                          fontWeight: FontWeight.w600,
                          letterSpacing: 4,
                          color: labelColor,
                        )),
                    const SizedBox(height: 14),
                    Expanded(
                      flex: 4,
                      child: _Captions(
                        user: _c.userCaption,
                        userIsPartial: _c.userPartial.isNotEmpty,
                        assistant: _c.assistantText,
                        streaming: _c.state == VoiceState.speaking ||
                            _c.state == VoiceState.thinking,
                        error: _c.error,
                      ),
                    ),
                    if (_showKeyboard)
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                        child: HudTextField(
                          controller: _typed,
                          hint: 'Type a message…',
                          icon: Icons.keyboard_outlined,
                          textInputAction: TextInputAction.send,
                          onSubmitted: (v) {
                            _c.sendText(v);
                            _typed.clear();
                          },
                        ),
                      ),
                    Padding(
                      padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                        children: [
                          _RoundButton(
                            icon: _c.muted ? Icons.mic_off : Icons.mic,
                            label: _c.muted ? 'Unmute' : 'Mute',
                            selected: _c.muted,
                            onPressed: _c.toggleMute,
                          ),
                          _RoundButton(
                            icon: Icons.stop_rounded,
                            label: 'Stop',
                            onPressed: speaking || _c.state == VoiceState.thinking
                                ? _c.interrupt
                                : null,
                          ),
                          _RoundButton(
                            icon: Icons.call_end,
                            label: 'End',
                            danger: true,
                            onPressed: _end,
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

class _Captions extends StatelessWidget {
  final String user;
  final bool userIsPartial;
  final String assistant;
  final bool streaming;
  final String? error;
  const _Captions({
    required this.user,
    required this.userIsPartial,
    required this.assistant,
    required this.streaming,
    required this.error,
  });

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    if (user.isEmpty && assistant.isEmpty && error == null) {
      return const SizedBox.shrink();
    }
    // HudPanel shrink-wraps its child in a Column, which hands a scroll view UNBOUNDED height: the scroll
    // view then sizes itself to the text and the panel overflows by (text height - the space this slot
    // has). Bound it to the slot instead, so a long reply scrolls (and a short one still shrink-wraps).
    return LayoutBuilder(
      builder: (context, slot) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 20),
        child: HudPanel(
          blur: true,
          padding: EdgeInsets.zero,
          child: ConstrainedBox(
            constraints: BoxConstraints(maxHeight: slot.maxHeight),
            child: SingleChildScrollView(
              reverse: true,
              padding: const EdgeInsets.all(PaiSpacing.lg),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (user.isNotEmpty) ...[
                    Text('YOU', style: t.labelSmall?.copyWith(color: PaiColors.teal, letterSpacing: 3)),
                    const SizedBox(height: 4),
                    Text(
                      user,
                      style: t.bodyLarge?.copyWith(
                        color: scheme.onSurfaceVariant,
                        fontStyle: userIsPartial ? FontStyle.italic : FontStyle.normal,
                      ),
                    ),
                  ],
                  if (assistant.isNotEmpty) ...[
                    const SizedBox(height: 14),
                    Text('PAI', style: t.labelSmall?.copyWith(color: scheme.primary, letterSpacing: 3)),
                    const SizedBox(height: 4),
                    Wrap(
                      crossAxisAlignment: WrapCrossAlignment.center,
                      spacing: 4,
                      children: [
                        // Captions are plain text: never show raw markdown symbols (the reply may be partial).
                        Text(stripMarkdown(assistant), style: t.bodyLarge?.copyWith(color: scheme.onSurface, fontSize: 17)),
                        if (streaming) const BlinkingCursor(),
                      ],
                    ),
                  ],
                  if (error != null) ...[
                    const SizedBox(height: 14),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(Icons.error_outline, size: 18, color: scheme.error),
                        const SizedBox(width: 8),
                        Expanded(child: Text(error!, style: t.bodyMedium?.copyWith(color: scheme.error))),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _RoundButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback? onPressed;
  final bool selected;
  final bool danger;
  const _RoundButton({
    required this.icon,
    required this.label,
    required this.onPressed,
    this.selected = false,
    this.danger = false,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = danger
        ? scheme.error
        : selected
            ? PaiColors.amber
            : scheme.primary;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        HudIconButton(
          icon: icon,
          tooltip: label,
          onPressed: onPressed,
          color: color,
          filled: selected || danger,
          active: true,
          size: 64,
        ),
        const SizedBox(height: 6),
        Text(label.toUpperCase(),
            style: Theme.of(context).textTheme.labelSmall?.copyWith(
                color: onPressed == null ? scheme.onSurfaceVariant.withValues(alpha: 0.5) : scheme.onSurfaceVariant,
                letterSpacing: 2)),
      ],
    );
  }
}
