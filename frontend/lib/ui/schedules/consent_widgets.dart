import 'dart:convert';

import 'package:flutter/material.dart';

import '../../models/schedule.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';

/// Wording shared by the editor banner, the list card and tests.
const String kConsentNeededMessage = 'This action needs your approval to run on its own.';
const String kConsentResetMessage = 'Approval was reset because you changed the action.';

/// The approval durations offered (days). 30 is preselected.
const List<int> kConsentDurations = [7, 30, 90];
const int kDefaultConsentDays = 30;

String _date(DateTime d) {
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return '${months[d.month - 1]} ${d.day}, ${d.year}';
}

/// "Approved until Dec 23, 2026" — also used by tests.
String consentChipLabel(ConsentStatus status, DateTime? expiresAt) => switch (status) {
      ConsentStatus.notRequired => 'No approval needed',
      ConsentStatus.needsApproval => 'Needs approval',
      ConsentStatus.approved =>
        expiresAt == null ? 'Approved' : 'Approved until ${_date(expiresAt.toLocal())}',
      ConsentStatus.expiringSoon =>
        expiresAt == null ? 'Expires soon' : 'Expires soon · ${_date(expiresAt.toLocal())}',
      ConsentStatus.expired => 'Approval expired',
    };

/// One-line, human summary of a parameters object for confirmation text.
String parametersSummary(Map<String, dynamic> params) {
  if (params.isEmpty) return 'none';
  return params.entries
      .map((e) => '${e.key}: ${e.value is String ? e.value : jsonEncode(e.value)}')
      .join('\n');
}

class RiskChip extends StatelessWidget {
  const RiskChip(this.risk, {super.key, this.compact = true});
  final ScheduleRisk risk;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final p = context.pai;
    final c = switch (risk) {
      ScheduleRisk.low => p.green,
      ScheduleRisk.medium => p.amber,
      ScheduleRisk.high || ScheduleRisk.critical => p.red,
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
          Icon(risk == ScheduleRisk.critical ? Icons.warning_amber_rounded : Icons.shield_outlined,
              size: compact ? 12 : 14, color: c),
          const SizedBox(width: 5),
          Text(label,
              style: TextStyle(
                  color: c, fontSize: compact ? 11 : 12, fontWeight: FontWeight.w600)),
        ]),
      ),
    );
  }
}

/// Approval state of a schedule as a chip ("Approved until …", "Needs approval", ...).
class ConsentChip extends StatelessWidget {
  const ConsentChip({super.key, required this.status, this.expiresAt, this.compact = true});
  final ConsentStatus status;
  final DateTime? expiresAt;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    if (status == ConsentStatus.notRequired) return const SizedBox.shrink();
    final hud = switch (status) {
      ConsentStatus.approved => HudStatus.online,
      ConsentStatus.expiringSoon => HudStatus.paused,
      ConsentStatus.needsApproval => HudStatus.paused,
      ConsentStatus.expired => HudStatus.failed,
      ConsentStatus.notRequired => HudStatus.info,
    };
    return StatusChip(status: hud, label: consentChipLabel(status, expiresAt), compact: compact);
  }
}

/// Calm, prominent notice that an action needs standing approval, with the one button that
/// grants it. Approval never happens implicitly — only by pressing [onApprove].
class ConsentBanner extends StatelessWidget {
  const ConsentBanner({
    super.key,
    this.message = kConsentNeededMessage,
    this.detail,
    this.onApprove,
    this.approveEnabled = true,
    this.disabledReason,
    this.busy = false,
  });

  final String message;
  final String? detail;
  final VoidCallback? onApprove;
  final bool approveEnabled;

  /// Shown instead of the button hint when approval is temporarily unavailable
  /// (e.g. unsaved changes).
  final String? disabledReason;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final p = context.pai;
    final text = Theme.of(context).textTheme;
    return Semantics(
      container: true,
      label: message,
      child: HudPanel(
        accent: p.amber,
        brackets: false,
        padding: const EdgeInsets.all(PaiSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(Icons.verified_user_outlined, color: p.amber, size: 22),
              const SizedBox(width: PaiSpacing.sm),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(message, style: text.titleSmall),
                  if (detail != null) ...[
                    const SizedBox(height: 2),
                    Text(detail!, style: text.bodySmall?.copyWith(color: p.muted)),
                  ],
                ]),
              ),
            ]),
            if (onApprove != null) ...[
              const SizedBox(height: PaiSpacing.sm),
              Align(
                alignment: Alignment.centerRight,
                child: GlowButton(
                  label: 'Approve…',
                  icon: Icons.verified_outlined,
                  dense: true,
                  loading: busy,
                  onPressed: approveEnabled ? onApprove : null,
                ),
              ),
              if (!approveEnabled && disabledReason != null)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text(disabledReason!,
                      textAlign: TextAlign.end,
                      style: text.bodySmall?.copyWith(color: p.muted)),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Everything the approve dialog states, so the user sees EXACTLY what will run.
class ApprovalRequest {
  final String capability;
  final String deviceLabel;
  final Map<String, dynamic> parameters;
  final String scheduleLabel;
  final ScheduleRisk risk;
  const ApprovalRequest({
    required this.capability,
    required this.deviceLabel,
    required this.parameters,
    required this.scheduleLabel,
    required this.risk,
  });

  factory ApprovalRequest.fromSchedule(Schedule s, {required String deviceLabel}) => ApprovalRequest(
        capability: s.capability,
        deviceLabel: deviceLabel,
        parameters: s.parameters,
        scheduleLabel: s.repeatLabel,
        risk: s.risk,
      );
}

/// Explicit confirmation. Returns the chosen number of days, or null if cancelled.
Future<int?> showApproveConsentDialog(BuildContext context, ApprovalRequest req) {
  return showDialog<int>(
    context: context,
    builder: (_) => ApproveConsentDialog(request: req),
  );
}

class ApproveConsentDialog extends StatefulWidget {
  const ApproveConsentDialog({super.key, required this.request});
  final ApprovalRequest request;

  @override
  State<ApproveConsentDialog> createState() => _ApproveConsentDialogState();
}

class _ApproveConsentDialogState extends State<ApproveConsentDialog> {
  int _days = kDefaultConsentDays;

  Widget _row(BuildContext context, String label, Widget value) => Padding(
        padding: const EdgeInsets.only(bottom: PaiSpacing.sm),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(
            width: 92,
            child: Text(label,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(color: context.pai.muted)),
          ),
          Expanded(child: value),
        ]),
      );

  @override
  Widget build(BuildContext context) {
    final r = widget.request;
    final text = Theme.of(context).textTheme;
    final until = PaiClock.now().add(Duration(days: _days));
    return AlertDialog(
      title: const Text('Approve this to run on its own?'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'It will run on the schedule below without asking you each time, until the '
              'approval ends. You can revoke it at any time.',
              style: text.bodySmall?.copyWith(color: context.pai.muted),
            ),
            const SizedBox(height: PaiSpacing.md),
            _row(context, 'Action', Text(r.capability, style: PaiText.mono(size: 13, color: context.pai.strong))),
            _row(context, 'Device', Text(r.deviceLabel)),
            _row(context, 'Parameters',
                Text(parametersSummary(r.parameters), style: PaiText.mono(size: 12, color: context.pai.strong))),
            _row(context, 'Runs', Text(r.scheduleLabel)),
            _row(context, 'Risk', Align(alignment: Alignment.centerLeft, child: RiskChip(r.risk))),
            const SizedBox(height: PaiSpacing.sm),
            Text('Approve for', style: text.labelLarge),
            const SizedBox(height: PaiSpacing.xs),
            SegmentedButton<int>(
              showSelectedIcon: false,
              style: const ButtonStyle(minimumSize: WidgetStatePropertyAll(Size(0, 48))),
              segments: [
                for (final d in kConsentDurations)
                  ButtonSegment<int>(value: d, label: Text('$d days')),
              ],
              selected: {_days},
              onSelectionChanged: (s) => setState(() => _days = s.first),
            ),
            const SizedBox(height: PaiSpacing.sm),
            Text('Until ${_date(until)}', style: text.bodySmall?.copyWith(color: context.pai.muted)),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(
          onPressed: () => Navigator.pop(context, _days),
          child: const Text('Approve'),
        ),
      ],
    );
  }
}
