import 'package:flutter/material.dart';

import '../../theme/theme.dart';

enum GlowVariant { primary, outline, danger, ghost }

/// Primary action button with a soft cyan glow. ≥ 48dp tall, text-scale safe.
class GlowButton extends StatelessWidget {
  const GlowButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.variant = GlowVariant.primary,
    this.expanded = false,
    this.loading = false,
    this.dense = false,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final GlowVariant variant;
  final bool expanded;
  final bool loading;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final enabled = onPressed != null && !loading;
    final base = variant == GlowVariant.danger ? scheme.error : scheme.primary;
    final filled = variant == GlowVariant.primary;
    final fg = !enabled
        ? scheme.onSurfaceVariant.withValues(alpha: 0.6)
        : filled ? scheme.onPrimary : base;
    final bg = !enabled
        ? scheme.surfaceContainerHighest
        : filled ? base : (variant == GlowVariant.danger ? base.withValues(alpha: 0.08) : Colors.transparent);

    final content = Row(
      mainAxisSize: expanded ? MainAxisSize.max : MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        if (loading)
          SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: fg))
        else if (icon != null)
          Icon(icon, size: 20, color: fg),
        if (loading || icon != null) const SizedBox(width: 8),
        Flexible(
          child: Text(
            label.toUpperCase(),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.labelLarge?.copyWith(color: fg, letterSpacing: 1.4),
          ),
        ),
      ],
    );

    return Semantics(
      button: true,
      enabled: enabled,
      label: label,
      excludeSemantics: true,
      child: DecoratedBox(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(PaiRadii.md),
          // Only the primary action glows; outline/danger keep a clean single border.
          boxShadow: enabled && filled
              ? [BoxShadow(color: base.withValues(alpha: 0.35), blurRadius: 18, spreadRadius: -4)]
              : null,
        ),
        child: Material(
          color: bg,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(PaiRadii.md),
            side: variant == GlowVariant.primary || variant == GlowVariant.ghost
                ? BorderSide.none
                : BorderSide(color: enabled ? base.withValues(alpha: 0.7) : scheme.outlineVariant),
          ),
          child: InkWell(
            borderRadius: BorderRadius.circular(PaiRadii.md),
            onTap: enabled ? onPressed : null,
            child: ConstrainedBox(
              constraints: BoxConstraints(minHeight: dense ? 40 : kMinTouch, minWidth: kMinTouch),
              child: Padding(
                padding: EdgeInsets.symmetric(horizontal: dense ? 14 : 20, vertical: 10),
                child: content,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Round icon button with a hairline ring. [tooltip] is mandatory: it is the screen-reader label.
class HudIconButton extends StatelessWidget {
  const HudIconButton({
    super.key,
    required this.icon,
    required this.tooltip,
    required this.onPressed,
    this.active = false,
    this.color,
    this.size = kMinTouch,
    this.filled = false,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;
  final bool active;
  final Color? color;
  final double size;
  final bool filled;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final c = color ?? (active ? scheme.primary : scheme.onSurfaceVariant);
    final enabled = onPressed != null;
    return Tooltip(
      message: tooltip,
      child: Semantics(
        button: true,
        enabled: enabled,
        label: tooltip,
        excludeSemantics: true,
        child: SizedBox(
          width: size < kMinTouch ? kMinTouch : size,
          height: size < kMinTouch ? kMinTouch : size,
          child: Center(
            child: Material(
              color: filled ? c.withValues(alpha: 0.16) : (active ? c.withValues(alpha: 0.10) : Colors.transparent),
              shape: CircleBorder(side: BorderSide(color: c.withValues(alpha: enabled ? (active || filled ? 0.8 : 0.35) : 0.15))),
              child: InkWell(
                customBorder: const CircleBorder(),
                onTap: onPressed,
                child: SizedBox(
                  width: size,
                  height: size,
                  child: Icon(icon, size: size * 0.46, color: enabled ? c : c.withValues(alpha: 0.4)),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
