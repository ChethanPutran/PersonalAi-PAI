import 'package:flutter/material.dart';

import '../../../plugins/models/plugin_info.dart';
import '../../../plugins/models/plugin_update.dart';
import '../../theme/theme.dart';

/// "What's new" for a plugin update: release notes of every version being skipped and the
/// exact capabilities gained/lost, so the user knows what the update changes before accepting.
///
/// Resolves to true if the user tapped Update. [onUpdate] == null hides the button (read-only).
Future<bool?> showPluginUpdateSheet(
  BuildContext context, {
  required PluginInfo plugin,
  required PluginUpdate update,
  bool canUpdate = true,
}) {
  return showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (_) => _UpdateSheet(plugin: plugin, update: update, canUpdate: canUpdate),
  );
}

class _UpdateSheet extends StatelessWidget {
  final PluginInfo plugin;
  final PluginUpdate update;
  final bool canUpdate;
  const _UpdateSheet({required this.plugin, required this.update, required this.canUpdate});

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final blocked = update.blocked;
    final hasUpdate = update.updateAvailable && update.latestVersion != null;

    return SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.85),
        child: ListView(
          shrinkWrap: true,
          padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, 0, PaiSpacing.lg, PaiSpacing.lg),
          children: [
            Text(plugin.name, style: text.titleLarge),
            const SizedBox(height: 2),
            Text(
              hasUpdate
                  ? 'v${update.installedVersion} → v${update.latestVersion}'
                  : 'v${update.installedVersion}',
              style: context.pai.mono(size: 13),
            ),
            if (update.addedCapabilities.isNotEmpty ||
                update.removedCapabilities.isNotEmpty ||
                update.changedCapabilities.isNotEmpty) ...[
              const SizedBox(height: PaiSpacing.md),
              Text('Capabilities', style: text.titleSmall),
              for (final c in update.addedCapabilities)
                _line(context, Icons.add_circle_outline, 'New: $c', PaiColors.green),
              for (final c in update.changedCapabilities)
                _line(context, Icons.change_circle_outlined, 'Changed: $c', null),
              for (final c in update.removedCapabilities)
                _line(context, Icons.remove_circle_outline, 'Removed: $c', PaiColors.red),
            ],
            for (final entry in update.changelog.reversed) ...[
              const SizedBox(height: PaiSpacing.md),
              Text('v${entry.version}', style: text.titleSmall),
              if (entry.isEmpty)
                Text('No release notes.', style: TextStyle(color: context.pai.muted)),
              if (entry.summary.isNotEmpty) Text(entry.summary),
              for (final f in entry.features) _line(context, Icons.auto_awesome_outlined, f, null),
              for (final f in entry.fixes) _line(context, Icons.build_circle_outlined, f, null),
              for (final b in entry.breaking)
                _line(context, Icons.warning_amber_rounded, 'Breaking: $b', PaiColors.red),
            ],
            if (blocked != null) ...[
              const SizedBox(height: PaiSpacing.md),
              _line(
                context,
                Icons.info_outline,
                'v${blocked.version} is available but not usable here: ${blocked.reason}',
                null,
              ),
            ],
            const SizedBox(height: PaiSpacing.lg),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: () => Navigator.of(context).pop(false),
                  child: const Text('Close'),
                ),
                if (hasUpdate && canUpdate) ...[
                  const SizedBox(width: PaiSpacing.sm),
                  FilledButton(
                    onPressed: () => Navigator.of(context).pop(true),
                    child: Text('Update to v${update.latestVersion}'),
                  ),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _line(BuildContext context, IconData icon, String label, Color? color) => Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, size: 16, color: color),
            const SizedBox(width: 8),
            Expanded(child: Text(label, style: color == null ? null : TextStyle(color: color))),
          ],
        ),
      );
}
