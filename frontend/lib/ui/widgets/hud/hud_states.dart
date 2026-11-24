import 'package:flutter/material.dart';

import '../../theme/theme.dart';
import 'glow_button.dart';
import 'hud_panel.dart';

/// Empty list / nothing-here state.
class HudEmptyState extends StatelessWidget {
  const HudEmptyState({super.key, required this.icon, required this.title, this.message, this.action});
  final IconData icon;
  final String title;
  final String? message;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(PaiSpacing.xl),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 380),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 72,
                height: 72,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(color: scheme.primary.withValues(alpha: 0.4)),
                  boxShadow: [BoxShadow(color: scheme.primary.withValues(alpha: 0.18), blurRadius: 24, spreadRadius: -4)],
                ),
                child: Icon(icon, size: 30, color: scheme.primary),
              ),
              const SizedBox(height: PaiSpacing.lg),
              Text(title.toUpperCase(), textAlign: TextAlign.center, style: text.titleMedium?.copyWith(letterSpacing: 2)),
              if (message != null) ...[
                const SizedBox(height: PaiSpacing.sm),
                Text(message!, textAlign: TextAlign.center, style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant)),
              ],
              if (action != null) ...[const SizedBox(height: PaiSpacing.lg), action!],
            ],
          ),
        ),
      ),
    );
  }
}

/// Failure state with a Retry action and the technical reason (selectable) underneath.
class HudErrorState extends StatelessWidget {
  const HudErrorState({super.key, required this.title, this.message, this.details, this.onRetry, this.retryLabel = 'Retry'});
  final String title;
  final String? message;
  final String? details;
  final VoidCallback? onRetry;
  final String retryLabel;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(PaiSpacing.xl),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: HudPanel(
            accent: scheme.error,
            padding: const EdgeInsets.all(PaiSpacing.xl),
            semanticLabel: 'Error: $title',
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  Icon(Icons.warning_amber_rounded, color: scheme.error, size: 26),
                  const SizedBox(width: 10),
                  Expanded(child: Text(title.toUpperCase(), style: text.titleMedium?.copyWith(color: scheme.error, letterSpacing: 1.6))),
                ]),
                if (message != null) ...[
                  const SizedBox(height: PaiSpacing.md),
                  Text(message!, style: text.bodyMedium),
                ],
                if (details != null) ...[
                  const SizedBox(height: PaiSpacing.md),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(PaiSpacing.md),
                    decoration: BoxDecoration(
                      color: Colors.black.withValues(alpha: 0.25),
                      borderRadius: BorderRadius.circular(PaiRadii.sm),
                      border: Border.all(color: scheme.error.withValues(alpha: 0.25)),
                    ),
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxHeight: 132),
                      child: SingleChildScrollView(
                        child: SelectableText(
                          details!,
                          style: PaiText.mono(size: 12, color: scheme.onSurfaceVariant),
                        ),
                      ),
                    ),
                  ),
                ],
                if (onRetry != null) ...[
                  const SizedBox(height: PaiSpacing.lg),
                  Align(
                    alignment: Alignment.centerRight,
                    child: GlowButton(label: retryLabel, icon: Icons.refresh, onPressed: onRetry, variant: GlowVariant.outline),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Shimmering placeholder block.
class HudSkeleton extends StatefulWidget {
  const HudSkeleton({super.key, this.height = 16, this.width, this.radius = PaiRadii.sm});
  final double height;
  final double? width;
  final double radius;

  @override
  State<HudSkeleton> createState() => _HudSkeletonState();
}

class _HudSkeletonState extends State<HudSkeleton> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 1500));

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (MediaQuery.disableAnimationsOf(context)) {
      _c.stop();
    } else if (!_c.isAnimating) {
      _c.repeat();
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final base = Theme.of(context).colorScheme.surfaceContainerHigh;
    final hi = Theme.of(context).colorScheme.primary.withValues(alpha: 0.18);
    return AnimatedBuilder(
      animation: _c,
      builder: (_, _) => Container(
        width: widget.width,
        height: widget.height,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(widget.radius),
          gradient: LinearGradient(
            begin: Alignment(-1 + 3 * _c.value - 1, 0),
            end: Alignment(1 + 3 * _c.value - 1, 0),
            colors: [base, hi, base],
            stops: const [0.3, 0.5, 0.7],
          ),
        ),
      ),
    );
  }
}

/// A few panel-shaped skeleton rows, used while a list loads.
class HudSkeletonList extends StatelessWidget {
  const HudSkeletonList({super.key, this.count = 4});
  final int count;

  @override
  Widget build(BuildContext context) {
    return ListView.separated(
      physics: const NeverScrollableScrollPhysics(),
      padding: const EdgeInsets.all(PaiSpacing.lg),
      itemCount: count,
      separatorBuilder: (_, _) => const SizedBox(height: PaiSpacing.md),
      itemBuilder: (_, _) => const HudPanel(
        brackets: false,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            HudSkeleton(height: 16, width: 180),
            SizedBox(height: 10),
            HudSkeleton(height: 12),
            SizedBox(height: 6),
            HudSkeleton(height: 12, width: 120),
          ],
        ),
      ),
    );
  }
}
