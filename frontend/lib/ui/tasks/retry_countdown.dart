import 'dart:async';

import 'package:flutter/material.dart';

import '../../../tasks/task.dart';
import '../theme/theme.dart';

/// "Next attempt in 12 s · Device did not respond in 60s" while a task backs off before a retry.
///
/// The countdown is a LOCAL display: the server sent an absolute `next_retry_at`, and a UI-only
/// one-second ticker re-renders the remaining time against [PaiClock.now]. It never touches the
/// network. The ticker exists only while the widget is mounted (the parent drops this widget as
/// soon as the task leaves RETRYING) and is cancelled in [dispose].
class RetryCountdown extends StatefulWidget {
  final TaskRetry retry;
  const RetryCountdown({super.key, required this.retry});

  @override
  State<RetryCountdown> createState() => _RetryCountdownState();

  /// "12 s", "1 m 05 s", "2 h 03 m".
  static String formatRemaining(Duration d) {
    final s = d.inSeconds < 0 ? 0 : d.inSeconds;
    if (s < 60) return '$s s';
    if (s < 3600) return '${s ~/ 60} m ${(s % 60).toString().padLeft(2, '0')} s';
    return '${s ~/ 3600} h ${((s % 3600) ~/ 60).toString().padLeft(2, '0')} m';
  }
}

class _RetryCountdownState extends State<RetryCountdown> {
  Timer? _ticker;
  bool _expanded = false;

  @override
  void initState() {
    super.initState();
    _syncTicker();
  }

  @override
  void didUpdateWidget(RetryCountdown old) {
    super.didUpdateWidget(old);
    _syncTicker();
  }

  Duration? get _remaining {
    final at = widget.retry.nextRetryAt;
    if (at == null) return null;
    final d = at.difference(PaiClock.now());
    return d.isNegative ? Duration.zero : d;
  }

  void _syncTicker() {
    final r = _remaining;
    final needed = r != null && r > Duration.zero;
    if (needed && _ticker == null) {
      _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
        if (!mounted) return;
        setState(() {});
        if ((_remaining ?? Duration.zero) <= Duration.zero) _stopTicker();
      });
    } else if (!needed) {
      _stopTicker();
    }
  }

  void _stopTicker() {
    _ticker?.cancel();
    _ticker = null;
  }

  @override
  void dispose() {
    _stopTicker();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final amber = context.pai.amber;
    final r = _remaining;
    final err = widget.retry.lastError;
    final head = r == null
        ? 'Retrying soon'
        : r > Duration.zero
            ? 'Next attempt in ${RetryCountdown.formatRemaining(r)}'
            : 'Retrying now…';
    final line = (err != null && err.isNotEmpty && !_expanded) ? '$head · $err' : head;
    final canExpand = err != null && err.isNotEmpty;

    return Semantics(
      container: true,
      label: canExpand ? '$head. Last error: $err' : head,
      child: ExcludeSemantics(
        child: InkWell(
          key: const Key('retry-countdown'),
          onTap: canExpand ? () => setState(() => _expanded = !_expanded) : null,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.schedule, size: 16, color: amber),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      line,
                      key: const Key('retry-countdown-text'),
                      maxLines: _expanded ? null : 2,
                      overflow: _expanded ? TextOverflow.visible : TextOverflow.ellipsis,
                      style: text.bodySmall?.copyWith(color: amber),
                    ),
                  ),
                  if (canExpand)
                    Icon(_expanded ? Icons.expand_less : Icons.expand_more, size: 16, color: amber),
                ],
              ),
              if (_expanded && canExpand)
                Padding(
                  padding: const EdgeInsets.only(left: 22, top: 4),
                  child: SelectableText(
                    err,
                    key: const Key('retry-last-error'),
                    style: text.bodySmall?.copyWith(color: context.pai.muted),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
