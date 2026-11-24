import 'package:flutter/material.dart';

import '../../theme/theme.dart';

/// "▌ SECTION TITLE ─────────  action" — groups content on every screen.
class HudSectionHeader extends StatelessWidget {
  const HudSectionHeader(this.title, {super.key, this.trailing, this.padding});
  final String title;
  final Widget? trailing;
  final EdgeInsetsGeometry? padding;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: padding ?? const EdgeInsets.fromLTRB(PaiSpacing.lg, PaiSpacing.lg, PaiSpacing.lg, PaiSpacing.sm),
      child: Semantics(
        header: true,
        child: Row(
          children: [
            Container(width: 3, height: 16, color: scheme.primary),
            const SizedBox(width: 10),
            Flexible(
              child: Text(
                title.toUpperCase(),
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.labelMedium?.copyWith(color: scheme.primary, letterSpacing: 2),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Container(
                height: 1,
                decoration: BoxDecoration(
                  gradient: LinearGradient(colors: [scheme.primary.withValues(alpha: 0.4), Colors.transparent]),
                ),
              ),
            ),
            if (trailing != null) ...[const SizedBox(width: 8), trailing!],
          ],
        ),
      ),
    );
  }
}
