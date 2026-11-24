import 'package:flutter/material.dart';

import '../../theme/theme.dart';

/// Visual status vocabulary shared by tasks, devices, schedules and plugins.
enum HudStatus { running, queued, paused, completed, failed, cancelled, retrying, online, offline, info }

extension HudStatusX on HudStatus {
  Color get color => switch (this) {
        HudStatus.running => PaiColors.cyan,
        HudStatus.queued => PaiColors.azure,
        HudStatus.paused => PaiColors.amber,
        HudStatus.retrying => PaiColors.amber,
        HudStatus.completed => PaiColors.green,
        HudStatus.online => PaiColors.green,
        HudStatus.failed => PaiColors.red,
        HudStatus.cancelled => PaiColors.textLow,
        HudStatus.offline => PaiColors.textLow,
        HudStatus.info => PaiColors.azure,
      };

  String get label => switch (this) {
        HudStatus.running => 'Running',
        HudStatus.queued => 'Queued',
        HudStatus.paused => 'Paused',
        HudStatus.retrying => 'Retrying',
        HudStatus.completed => 'Completed',
        HudStatus.failed => 'Failed',
        HudStatus.cancelled => 'Cancelled',
        HudStatus.online => 'Online',
        HudStatus.offline => 'Offline',
        HudStatus.info => 'Info',
      };

  /// Colour for the current brightness (darker variants on light surfaces).
  Color colorOf(BuildContext context) {
    final p = context.pai;
    return switch (this) {
      HudStatus.running => p.cyan,
      HudStatus.queued || HudStatus.info => p.azure,
      HudStatus.paused || HudStatus.retrying => p.amber,
      HudStatus.completed || HudStatus.online => p.green,
      HudStatus.failed => p.red,
      HudStatus.cancelled || HudStatus.offline => p.faint,
    };
  }

  bool get pulses => this == HudStatus.running || this == HudStatus.retrying || this == HudStatus.online;

  /// Maps a backend `TaskStatus` wire name ("running", "initialized"...) to a visual status.
  static HudStatus fromTaskStatus(String name) => switch (name) {
        'running' => HudStatus.running,
        'queued' || 'initialized' || 'created' => HudStatus.queued,
        'paused' => HudStatus.paused,
        'retrying' => HudStatus.retrying,
        'completed' => HudStatus.completed,
        'failed' => HudStatus.failed,
        'cancelled' => HudStatus.cancelled,
        _ => HudStatus.info,
      };
}

class StatusDot extends StatefulWidget {
  const StatusDot({super.key, required this.status, this.size = 9, this.pulse});
  final HudStatus status;
  final double size;
  final bool? pulse;

  @override
  State<StatusDot> createState() => _StatusDotState();
}

class _StatusDotState extends State<StatusDot> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 1400));

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _sync();
  }

  @override
  void didUpdateWidget(StatusDot old) {
    super.didUpdateWidget(old);
    _sync();
  }

  void _sync() {
    final run = (widget.pulse ?? widget.status.pulses) && !MediaQuery.disableAnimationsOf(context);
    if (run && !_c.isAnimating) {
      _c.repeat(reverse: true);
    } else if (!run && _c.isAnimating) {
      _c.stop();
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color = widget.status.colorOf(context);
    return AnimatedBuilder(
      animation: _c,
      builder: (_, _) {
        final g = 0.25 + 0.35 * _c.value;
        return Container(
          width: widget.size,
          height: widget.size,
          decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
            boxShadow: [BoxShadow(color: color.withValues(alpha: g), blurRadius: 8, spreadRadius: 1)],
          ),
        );
      },
    );
  }
}

/// Compact status pill: coloured dot + uppercase label.
class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.status, this.label, this.compact = false});
  final HudStatus status;
  final String? label;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final c = status.colorOf(context);
    final text = label ?? status.label;
    return Semantics(
      label: 'Status: $text',
      excludeSemantics: true,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: compact ? 8 : 10, vertical: compact ? 3 : 5),
        decoration: BoxDecoration(
          color: c.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(PaiRadii.pill),
          border: Border.all(color: c.withValues(alpha: 0.55)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            StatusDot(status: status, size: compact ? 7 : 8),
            const SizedBox(width: 7),
            Text(
              text.toUpperCase(),
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: c, letterSpacing: 1.2, fontSize: compact ? 11.5 : 12.5),
            ),
          ],
        ),
      ),
    );
  }
}
