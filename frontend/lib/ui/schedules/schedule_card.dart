import 'package:flutter/material.dart';

import '../../models/schedule.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';
import 'consent_widgets.dart';

enum ScheduleMenuAction { history, edit, toggle, revoke, delete }

HudStatus _runStatus(LastRunStatus s) => switch (s) {
      LastRunStatus.ok => HudStatus.completed,
      LastRunStatus.skippedNoConsent => HudStatus.paused,
      LastRunStatus.denied || LastRunStatus.failed => HudStatus.failed,
      LastRunStatus.deviceOffline => HudStatus.offline,
      LastRunStatus.unknown => HudStatus.info,
    };

/// One schedule in the reminders list: what it does (reminder or plugin action), where, when
/// next, its approval state and how the last run went.
class ScheduleCard extends StatefulWidget {
  const ScheduleCard({
    super.key,
    required this.schedule,
    required this.deviceLabel,
    required this.onToggle,
    required this.onMenu,
    this.onApprove,
  });

  final Schedule schedule;

  /// Name of the target device ("Pixel 8"), or "Any online device".
  final String deviceLabel;
  final VoidCallback onToggle;
  final ValueChanged<ScheduleMenuAction> onMenu;

  /// Shown on cards whose action is waiting for approval.
  final VoidCallback? onApprove;

  @override
  State<ScheduleCard> createState() => _ScheduleCardState();
}

class _ScheduleCardState extends State<ScheduleCard> {
  bool _showError = false;

  @override
  Widget build(BuildContext context) {
    final s = widget.schedule;
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final p = context.pai;
    final now = PaiClock.now();
    final consent = s.consentStatus(now);
    final plugin = !s.isReminder;
    final lastError = s.lastRunError;

    final icon = plugin
        ? (s.enabled ? Icons.bolt : Icons.bolt_outlined)
        : (s.enabled ? Icons.alarm : Icons.alarm_off);

    return HudPanel(
      onTap: widget.onToggle,
      accent: s.enabled ? null : p.faint,
      semanticLabel:
          '${plugin ? 'Plugin action' : 'Reminder'} ${s.title}, ${s.enabled ? 'enabled' : 'disabled'}',
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: s.enabled ? scheme.primary : p.faint, size: 26),
          const SizedBox(width: PaiSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        s.title,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: text.titleMedium?.copyWith(
                          decoration: s.enabled ? null : TextDecoration.lineThrough,
                          color: s.enabled ? null : p.muted,
                        ),
                      ),
                    ),
                    StatusChip(
                      status: s.enabled ? HudStatus.online : HudStatus.offline,
                      label: s.enabled ? 'Armed' : 'Off',
                      compact: true,
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                // What it does, and where
                if (plugin)
                  Wrap(
                    spacing: PaiSpacing.sm,
                    runSpacing: 4,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      Text(s.capability, style: PaiText.mono(size: 12, color: p.strong)),
                      RiskChip(s.risk),
                    ],
                  )
                else
                  Text('Reminder', style: text.bodySmall?.copyWith(color: p.muted)),
                const SizedBox(height: 2),
                Text('on ${widget.deviceLabel}', style: text.bodySmall?.copyWith(color: p.muted)),
                const SizedBox(height: 4),
                Text(s.repeatLabel, style: text.bodySmall),
                const SizedBox(height: 2),
                Text(s.nextLabel, style: PaiText.mono(size: 12, color: scheme.primary)),

                // Approval
                if (consent != ConsentStatus.notRequired) ...[
                  const SizedBox(height: PaiSpacing.sm),
                  ConsentChip(status: consent, expiresAt: s.consent.expiresAt),
                ],
                if (consent.blocksRunning && widget.onApprove != null) ...[
                  const SizedBox(height: PaiSpacing.sm),
                  _ApprovalRow(onApprove: widget.onApprove!),
                ],

                // Last run
                if (s.lastRunStatus != null) ...[
                  const SizedBox(height: PaiSpacing.sm),
                  Wrap(
                    spacing: PaiSpacing.sm,
                    runSpacing: 4,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      StatusChip(
                        status: _runStatus(s.lastRunStatus!),
                        label: s.lastRunStatus!.label,
                        compact: true,
                      ),
                      if (s.lastRunAt != null)
                        Text('Last run ${Schedule.formatDateTime(s.lastRunAt!)}',
                            style: text.bodySmall?.copyWith(color: p.muted)),
                      if (lastError != null && lastError.isNotEmpty)
                        InkWell(
                          onTap: () => setState(() => _showError = !_showError),
                          borderRadius: BorderRadius.circular(PaiRadii.sm),
                          child: ConstrainedBox(
                            constraints: const BoxConstraints(minHeight: 48),
                            child: Row(mainAxisSize: MainAxisSize.min, children: [
                              Text(_showError ? 'Hide details' : 'Details',
                                  style: text.labelMedium?.copyWith(color: scheme.primary)),
                              Icon(_showError ? Icons.expand_less : Icons.expand_more,
                                  size: 18, color: scheme.primary),
                            ]),
                          ),
                        ),
                    ],
                  ),
                  if (_showError && lastError != null)
                    Container(
                      key: const Key('last-run-error'),
                      width: double.infinity,
                      margin: const EdgeInsets.only(top: 4),
                      padding: const EdgeInsets.all(PaiSpacing.sm),
                      decoration: BoxDecoration(
                        color: p.codeBg,
                        borderRadius: BorderRadius.circular(PaiRadii.sm),
                      ),
                      child: Text(lastError, style: PaiText.mono(size: 12, color: p.red)),
                    ),
                ],
              ],
            ),
          ),
          PopupMenuButton<ScheduleMenuAction>(
            tooltip: 'Schedule actions',
            onSelected: widget.onMenu,
            itemBuilder: (_) => [
              const PopupMenuItem(value: ScheduleMenuAction.history, child: Text('History')),
              const PopupMenuItem(value: ScheduleMenuAction.edit, child: Text('Edit')),
              PopupMenuItem(
                value: ScheduleMenuAction.toggle,
                child: Text(s.enabled ? 'Disable' : 'Enable'),
              ),
              if (consent == ConsentStatus.approved || consent == ConsentStatus.expiringSoon)
                const PopupMenuItem(value: ScheduleMenuAction.revoke, child: Text('Revoke approval')),
              const PopupMenuItem(value: ScheduleMenuAction.delete, child: Text('Delete')),
            ],
          ),
        ],
      ),
    );
  }
}

/// Calm inline notice on a card: this needs approval, with the one button that grants it.
class _ApprovalRow extends StatelessWidget {
  const _ApprovalRow({required this.onApprove});
  final VoidCallback onApprove;

  @override
  Widget build(BuildContext context) {
    final p = context.pai;
    return Container(
      key: const Key('approval-row'),
      padding: const EdgeInsets.all(PaiSpacing.sm),
      decoration: BoxDecoration(
        color: p.amber.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(PaiRadii.md),
        border: Border.all(color: p.amber.withValues(alpha: 0.45)),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(Icons.verified_user_outlined, size: 18, color: p.amber),
          const SizedBox(width: PaiSpacing.sm),
          Expanded(
            child: Text(kConsentNeededMessage, style: Theme.of(context).textTheme.bodySmall),
          ),
        ]),
        const SizedBox(height: PaiSpacing.xs),
        Align(
          alignment: Alignment.centerRight,
          child: GlowButton(label: 'Approve…', icon: Icons.verified_outlined, dense: true, onPressed: onApprove),
        ),
      ]),
    );
  }
}
