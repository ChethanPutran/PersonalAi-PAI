import 'package:flutter/material.dart';

import '../../../plugins/permissions/permission_models.dart';
import '../../theme/theme.dart';
import '../../widgets/hud/hud.dart';

const List<String> _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// "Dec 20" (same year as the app clock) or "Dec 20, 2027".
String shortDate(DateTime d) {
  final local = d.toLocal();
  final now = PaiClock.now();
  final base = '${_months[local.month - 1]} ${local.day}';
  return local.year == now.year ? base : '$base, ${local.year}';
}

/// "Dec 20, 2026" — always with the year (for explicit "until" lines).
String fullDate(DateTime d) {
  final l = d.toLocal();
  return '${_months[l.month - 1]} ${l.day}, ${l.year}';
}

/// "5 min ago" / "yesterday" style, relative to the app clock.
String relativeTime(DateTime when) {
  final diff = PaiClock.now().difference(when.toLocal());
  if (diff.inSeconds < 60) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes} min ago';
  if (diff.inHours < 24) return '${diff.inHours} h ago';
  if (diff.inDays < 7) return '${diff.inDays} d ago';
  return shortDate(when);
}

/// Risk of the action being approved. Same visual language as the schedules risk chip.
class RiskBadge extends StatelessWidget {
  const RiskBadge(this.risk, {super.key, this.compact = true});
  final ConfirmationRisk risk;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final p = context.pai;
    final c = switch (risk) {
      ConfirmationRisk.low => p.green,
      ConfirmationRisk.medium => p.amber,
      ConfirmationRisk.high || ConfirmationRisk.critical => p.red,
    };
    final label = '${risk.label} risk';
    return Semantics(
      label: label,
      excludeSemantics: true,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: compact ? 8 : 10, vertical: compact ? 3 : 5),
        decoration: BoxDecoration(
          color: c.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(PaiRadii.pill),
          border: Border.all(color: c.withValues(alpha: 0.55)),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(risk == ConfirmationRisk.critical ? Icons.warning_amber_rounded : Icons.shield_outlined,
              size: compact ? 12 : 14, color: c),
          const SizedBox(width: 5),
          Text(label,
              style: TextStyle(color: c, fontSize: compact ? 11 : 12, fontWeight: FontWeight.w600)),
        ]),
      ),
    );
  }
}

/// "Session" / "Always" chip for a standing grant.
class ScopeChip extends StatelessWidget {
  const ScopeChip(this.scope, {super.key});
  final ConfirmationScope scope;

  @override
  Widget build(BuildContext context) {
    return switch (scope) {
      ConfirmationScope.session => const StatusChip(status: HudStatus.info, label: 'Session', compact: true),
      ConfirmationScope.always => const StatusChip(status: HudStatus.online, label: 'Always', compact: true),
      ConfirmationScope.once => const StatusChip(status: HudStatus.info, label: 'Once', compact: true),
    };
  }
}
