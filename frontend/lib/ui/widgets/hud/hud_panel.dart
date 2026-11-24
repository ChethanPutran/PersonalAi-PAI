import 'dart:ui' show ImageFilter;

import 'package:flutter/material.dart';

import '../../theme/theme.dart';
import 'hud_background.dart';

/// The basic container of the HUD look: dark glass fill, hairline cyan border, corner brackets
/// and an optional title strip. Use [blur] sparingly (it is expensive in long lists).
class HudPanel extends StatelessWidget {
  const HudPanel({
    super.key,
    required this.child,
    this.title,
    this.leadingIcon,
    this.trailing,
    this.padding = const EdgeInsets.all(PaiSpacing.lg),
    this.accent,
    this.blur = false,
    this.brackets = true,
    this.selected = false,
    this.onTap,
    this.semanticLabel,
  });

  final Widget child;
  final String? title;
  final IconData? leadingIcon;
  final Widget? trailing;
  final EdgeInsetsGeometry padding;
  final Color? accent;
  final bool blur;
  final bool brackets;
  final bool selected;
  final VoidCallback? onTap;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final color = accent ?? scheme.primary;
    final borderColor = selected ? color.withValues(alpha: 0.85) : color.withValues(alpha: dark ? 0.28 : 0.35);

    Widget body = Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (title != null) _TitleStrip(title: title!, icon: leadingIcon, trailing: trailing, color: color),
        Padding(padding: padding, child: child),
      ],
    );

    Widget panel = DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: PaiRadii.panel,
        border: Border.all(color: borderColor),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: dark
              ? [PaiColors.bg2.withValues(alpha: 0.88), PaiColors.bg1.withValues(alpha: 0.78)]
              : [scheme.surfaceContainer, scheme.surfaceContainerLow],
        ),
        boxShadow: selected
            ? [BoxShadow(color: color.withValues(alpha: 0.18), blurRadius: 18, spreadRadius: -2)]
            : null,
      ),
      child: Stack(
        children: [
          body,
          if (brackets && dark)
            Positioned.fill(
              child: IgnorePointer(
                child: CustomPaint(
                  painter: HudCornerBrackets(color: color.withValues(alpha: selected ? 1 : 0.75)),
                ),
              ),
            ),
        ],
      ),
    );

    if (blur) {
      panel = ClipRRect(
        borderRadius: PaiRadii.panel,
        child: BackdropFilter(filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10), child: panel),
      );
    }

    if (onTap != null) {
      panel = Material(
        type: MaterialType.transparency,
        child: InkWell(
          borderRadius: PaiRadii.panel,
          onTap: onTap,
          child: panel,
        ),
      );
    }
    return Semantics(container: true, label: semanticLabel, child: panel);
  }
}

class _TitleStrip extends StatelessWidget {
  const _TitleStrip({required this.title, this.icon, this.trailing, required this.color});
  final String title;
  final IconData? icon;
  final Widget? trailing;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Container(
      padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, PaiSpacing.sm + 2, PaiSpacing.md, PaiSpacing.sm + 2),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: color.withValues(alpha: 0.22))),
        gradient: LinearGradient(colors: [color.withValues(alpha: 0.10), Colors.transparent]),
      ),
      child: Row(
        children: [
          if (icon != null) ...[Icon(icon, size: 16, color: color), const SizedBox(width: 8)]
          else ...[
            Container(width: 6, height: 6, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
            const SizedBox(width: 8),
          ],
          Expanded(
            child: Text(
              title.toUpperCase(),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: text.labelMedium?.copyWith(color: color, letterSpacing: 1.8),
            ),
          ),
          ?trailing,
        ],
      ),
    );
  }
}
