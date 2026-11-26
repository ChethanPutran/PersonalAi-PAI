import 'package:flutter/material.dart';

import '../../../plugins/builtin_plugins.dart';
import '../../theme/theme.dart';
import '../../widgets/hud/hud.dart';
import 'accessibility_setup_notice.dart';

/// "Built-in capabilities": things the PAI backend provides itself, each with an on/off switch.
/// Shown above the per-device plugin list. Renders nothing until the list has loaded (so a failed
/// load leaves the rest of the screen exactly as it was).
class BuiltinCapabilitiesSection extends StatelessWidget {
  final BuiltinPluginsController controller;
  const BuiltinCapabilitiesSection({super.key, required this.controller});

  /// What the user is told for the agent (the server's own description is longer and technical).
  static const agentExplanation = 'Lets PAI work step by step on your devices, including controlling the screen. '
      'Asks before anything sensitive.';

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        if (!controller.loaded || controller.plugins.isEmpty) return const SizedBox.shrink();
        return Column(
          key: const Key('builtin-capabilities'),
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const HudSectionHeader('Built-in capabilities'),
            for (final p in controller.plugins)
              _BuiltinRow(plugin: p, busy: controller.busy.contains(p.id), onChanged: (v) => controller.setEnabled(p.id, v)),
            if (controller.error != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, 0, PaiSpacing.lg, PaiSpacing.sm),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(Icons.error_outline, size: 16, color: Theme.of(context).colorScheme.error),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(controller.error!,
                          key: const Key('builtin-error'),
                          style: Theme.of(context).textTheme.bodySmall?.copyWith(color: Theme.of(context).colorScheme.error)),
                    ),
                  ],
                ),
              ),
          ],
        );
      },
    );
  }
}

class _BuiltinRow extends StatelessWidget {
  final BuiltinPlugin plugin;
  final bool busy;
  final ValueChanged<bool> onChanged;
  const _BuiltinRow({required this.plugin, required this.busy, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final isAgent = plugin.id == 'agent';
    final explanation = isAgent ? BuiltinCapabilitiesSection.agentExplanation : plugin.description;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg, vertical: PaiSpacing.xs + 2),
      child: HudPanel(
        brackets: false,
        padding: const EdgeInsets.fromLTRB(PaiSpacing.md, PaiSpacing.md, PaiSpacing.md, PaiSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Icon(isAgent ? Icons.smart_toy_outlined : Icons.extension_outlined, color: scheme.primary),
                ),
                const SizedBox(width: PaiSpacing.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(plugin.name, style: text.titleMedium),
                      const SizedBox(height: 3),
                      Text(explanation, style: text.bodySmall),
                    ],
                  ),
                ),
                const SizedBox(width: PaiSpacing.sm),
                Switch(
                  key: Key('builtin-switch-${plugin.id}'),
                  value: plugin.enabled,
                  onChanged: busy ? null : onChanged,
                ),
              ],
            ),
            if (isAgent && plugin.enabled) ...[
              const SizedBox(height: PaiSpacing.sm),
              Text(
                'To control another device\'s screen, install the Computer control plugin on that device '
                '(Plugins, below). On Android it also needs the accessibility service turned on.',
                key: const Key('agent-computer-control-hint'),
                style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
              ),
              const AccessibilitySetupNotice(),
            ],
          ],
        ),
      ),
    );
  }
}
