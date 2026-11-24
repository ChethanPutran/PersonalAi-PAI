import 'package:flutter/material.dart';

import '../theme/theme.dart';

/// A calm, one-line notice shown while the live task feed is down:
/// "Live updates paused — reconnecting…". The feed retries on its own (with backoff), so this is
/// information, not a button; pull-to-refresh still works.
class LiveFeedNotice extends StatelessWidget {
  const LiveFeedNotice({super.key});

  static const message = 'Live updates paused — reconnecting…';

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final amber = context.pai.amber; // brightness-aware (AA contrast in light mode too)
    return Semantics(
      liveRegion: true,
      label: message,
      child: Container(
        key: const Key('live-feed-notice'),
        margin: const EdgeInsets.fromLTRB(PaiSpacing.lg, PaiSpacing.sm, PaiSpacing.lg, 0),
        padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.md, vertical: PaiSpacing.sm),
        decoration: BoxDecoration(
          color: amber.withValues(alpha: 0.10),
          borderRadius: BorderRadius.circular(PaiRadii.sm),
          border: Border.all(color: amber.withValues(alpha: 0.45)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(strokeWidth: 2, color: amber),
            ),
            const SizedBox(width: PaiSpacing.sm),
            Flexible(
              child: Text(message, style: text.bodySmall?.copyWith(color: amber)),
            ),
          ],
        ),
      ),
    );
  }
}
