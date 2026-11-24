import 'package:flutter/material.dart';

import '../../models/schedule_capability.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';
import 'consent_widgets.dart';

/// The field that shows the chosen action and opens [showCapabilityPicker].
class CapabilityPickerField extends StatelessWidget {
  const CapabilityPickerField({
    super.key,
    required this.capabilities,
    required this.selectedId,
    required this.onSelected,
    this.errorText,
    this.enabled = true,
  });

  final List<SchedulableCapability> capabilities;
  final String? selectedId;
  final ValueChanged<SchedulableCapability> onSelected;
  final String? errorText;
  final bool enabled;

  SchedulableCapability? get _selected =>
      capabilities.where((c) => c.id == selectedId).firstOrNull;

  @override
  Widget build(BuildContext context) {
    final sel = _selected;
    return Semantics(
      button: true,
      label: sel == null ? 'Choose an action' : 'Action ${sel.id}. Change',
      child: InkWell(
        borderRadius: BorderRadius.circular(PaiRadii.md),
        onTap: !enabled
            ? null
            : () async {
                final picked = await showCapabilityPicker(
                    context, capabilities: capabilities, selectedId: selectedId);
                if (picked != null) onSelected(picked);
              },
        child: InputDecorator(
          decoration: InputDecoration(
            labelText: 'Action',
            errorText: errorText,
            suffixIcon: const Icon(Icons.unfold_more),
          ),
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 28),
            child: sel == null
                ? Text('Choose an action…', style: TextStyle(color: context.pai.muted))
                : Row(children: [
                    Expanded(
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(sel.id, style: PaiText.mono(size: 13, color: context.pai.strong)),
                        if (sel.description.isNotEmpty)
                          Text(sel.description,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.bodySmall?.copyWith(color: context.pai.muted)),
                      ]),
                    ),
                    const SizedBox(width: PaiSpacing.sm),
                    RiskChip(sel.risk),
                  ]),
          ),
        ),
      ),
    );
  }
}

Future<SchedulableCapability?> showCapabilityPicker(
  BuildContext context, {
  required List<SchedulableCapability> capabilities,
  String? selectedId,
}) {
  return showModalBottomSheet<SchedulableCapability>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (_) => FractionallySizedBox(
      heightFactor: 0.85,
      child: CapabilityPickerSheet(capabilities: capabilities, selectedId: selectedId),
    ),
  );
}

/// Searchable list of actions grouped by plugin. Actions that cannot be scheduled are shown
/// disabled, with the reason, so the user learns why instead of finding them missing.
class CapabilityPickerSheet extends StatefulWidget {
  const CapabilityPickerSheet({super.key, required this.capabilities, this.selectedId});
  final List<SchedulableCapability> capabilities;
  final String? selectedId;

  @override
  State<CapabilityPickerSheet> createState() => _CapabilityPickerSheetState();
}

class _CapabilityPickerSheetState extends State<CapabilityPickerSheet> {
  String _query = '';

  List<SchedulableCapability> get _filtered {
    final q = _query.trim().toLowerCase();
    if (q.isEmpty) return widget.capabilities;
    return widget.capabilities
        .where((c) =>
            c.id.toLowerCase().contains(q) ||
            c.description.toLowerCase().contains(q) ||
            c.group.toLowerCase().contains(q))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    final items = _filtered;
    final groups = <String, List<SchedulableCapability>>{};
    for (final c in items) {
      groups.putIfAbsent(c.group, () => []).add(c);
    }
    final text = Theme.of(context).textTheme;
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, 0, PaiSpacing.lg, PaiSpacing.sm),
        child: HudTextField(
          label: 'Search actions',
          icon: Icons.search,
          autofocus: false,
          onChanged: (v) => setState(() => _query = v),
        ),
      ),
      Expanded(
        child: items.isEmpty
            ? const HudEmptyState(
                icon: Icons.search_off, title: 'No matching actions', message: 'Try a different search.')
            : ListView(
                padding: const EdgeInsets.only(bottom: PaiSpacing.xl),
                children: [
                  for (final g in groups.entries) ...[
                    Padding(
                      padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, PaiSpacing.md, PaiSpacing.lg, 4),
                      child: Semantics(
                        header: true,
                        child: Text(g.key.toUpperCase(),
                            style: text.labelSmall?.copyWith(color: context.pai.muted, letterSpacing: 1.2)),
                      ),
                    ),
                    for (final c in g.value) _tile(context, c),
                  ],
                ],
              ),
      ),
    ]);
  }

  Widget _tile(BuildContext context, SchedulableCapability c) {
    final text = Theme.of(context).textTheme;
    final enabled = c.schedulable;
    final muted = context.pai.muted;
    final selected = c.id == widget.selectedId;
    return Semantics(
      button: true,
      enabled: enabled,
      selected: selected,
      label: '${c.id}. ${c.description}. ${c.risk.label} risk.'
          '${enabled ? '' : ' Unavailable: ${c.blockedReason ?? 'cannot be scheduled'}.'}',
      excludeSemantics: true,
      child: InkWell(
        onTap: enabled ? () => Navigator.pop(context, c) : null,
        child: Opacity(
          opacity: enabled ? 1 : 0.55,
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 56),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg, vertical: PaiSpacing.sm),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Padding(
                  padding: const EdgeInsets.only(top: 2, right: PaiSpacing.md),
                  child: Icon(enabled ? (selected ? Icons.check_circle : Icons.bolt_outlined) : Icons.lock_outline,
                      size: 20, color: selected ? context.pai.cyan : muted),
                ),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(c.id, style: PaiText.mono(size: 13, color: context.pai.strong)),
                    if (c.description.isNotEmpty)
                      Text(c.description, style: text.bodySmall?.copyWith(color: muted)),
                    if (!enabled)
                      Padding(
                        padding: const EdgeInsets.only(top: 2),
                        child: Text(c.blockedReason ?? 'Cannot be scheduled',
                            style: text.bodySmall?.copyWith(color: context.pai.amber)),
                      ),
                  ]),
                ),
                const SizedBox(width: PaiSpacing.sm),
                RiskChip(c.risk),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}
