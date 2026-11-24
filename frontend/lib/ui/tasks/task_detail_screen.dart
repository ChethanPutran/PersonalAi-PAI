import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import '../../../tasks/task.dart';
import '../../../tasks/task_event_stream.dart';
import '../../../tasks/task_events.dart';
import '../../../tasks/task_service.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';
import 'live_feed_notice.dart';
import 'retry_countdown.dart';
import 'tasks_screen.dart' show AbandonedCancelNote;


class TaskDetailScreen extends StatefulWidget {
  final Task initialTask;
  final TaskService service;

  /// The live task feed. This task's state is replaced by each event that mentions it (events
  /// carry the whole task), so the screen updates the instant the server publishes — no polling.
  final TaskLiveFeed? live;

  const TaskDetailScreen({
    super.key,
    required this.initialTask,
    required this.service,
    this.live,
  });

  @override
  State<TaskDetailScreen> createState() => _TaskDetailScreenState();
}


class _TaskDetailScreenState extends State<TaskDetailScreen> {
  late Task _task;

  StreamSubscription<TaskStreamEvent>? _eventSub;
  StreamSubscription<TaskStreamConnection>? _connSub;
  TaskStreamConnection _conn = TaskStreamConnection.connected;
  bool _loading = false;
  bool _busy = false;   // prevents double-tapping pause/resume/cancel

  @override
  void initState() {
    super.initState();
    _task = widget.initialTask;

    final live = widget.live;
    if (live != null) {
      _conn = live.state;
      _eventSub = live.events.listen(_onLiveEvent, onError: (Object e) {
        debugPrint('[TaskDetail] live feed error: $e');
      });
      _connSub = live.connection.listen((c) {
        if (mounted && c != _conn) setState(() => _conn = c);
      });
    }

    // One fetch for the full record (the list entry may be a slimmer snapshot); after that the
    // feed keeps it current.
    unawaited(_refresh());
  }

  void _onLiveEvent(TaskStreamEvent event) {
    switch (event) {
      case TaskUpdated(:final task):
        if (task.id == _task.id && mounted) setState(() => _task = task);
      case TaskStreamResync():
        unawaited(_refresh());
      case TaskStreamReady():
        break;
    }
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    _connSub?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    if (_loading) return;
    _loading = true;
    if (mounted) setState(() {});
    try {
      final updated = await widget.service.getTask(_task.id);
      if (!mounted) return;
      setState(() => _task = updated);
    } catch (e, st) {
      debugPrint('[TaskDetail] refresh failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[TaskDetail] refresh', maxFrames: 6);
    } finally {
      _loading = false;
      if (mounted) setState(() {});
    }
  }

  Future<void> _action(
    String label,
    Future<void> Function(String id) op,
  ) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await op(_task.id);
      // Never optimistic: the confirming event updates this screen. If no event can arrive
      // (no feed / feed down) fetch once so the screen isn't left stale.
      final live = widget.live;
      if (live == null || live.state != TaskStreamConnection.connected) await _refresh();
    } on TaskControlException catch (e) {
      debugPrint('[TaskDetail] $label refused (${e.code}): ${e.message}');
      _snack(e.message);
    } catch (e, st) {
      debugPrint('[TaskDetail] $label failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[TaskDetail] $label', maxFrames: 6);
      _snack('$label failed: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    // What the controls offer is decided by the server per plugin capability.
    final canPause = _task.canPause;
    final canResume = _task.canResume;
    final canCancel = _task.canCancel;

    return HudScaffold(
      appBar: HudAppBar(
        titleWidget: Text(_task.title.toUpperCase(),
            maxLines: 1, overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.titleMedium?.copyWith(letterSpacing: 2)),
        actions: [
          HudIconButton(
            tooltip: 'Refresh',
            icon: Icons.refresh,
            onPressed: _loading ? null : _refresh,
          ),
          if (canPause)
            HudIconButton(
              tooltip: 'Pause',
              icon: Icons.pause,
              onPressed: _busy
                  ? null
                  : () => _action('Pause', widget.service.pause),
            ),
          if (canResume)
            HudIconButton(
              tooltip: 'Resume',
              icon: Icons.play_arrow,
              active: true,
              onPressed: _busy
                  ? null
                  : () => _action('Resume', widget.service.resume),
            ),
          if (canCancel)
            HudIconButton(
              tooltip: 'Cancel',
              icon: Icons.stop,
              color: Theme.of(context).colorScheme.error,
              onPressed: _busy
                  ? null
                  : () => _action('Cancel', widget.service.cancel),
            ),
          const SizedBox(width: 8),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: Align(
          alignment: Alignment.topCenter,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 860),
            child: ListView(
          padding: const EdgeInsets.all(PaiSpacing.lg),
          children: [
            if (_conn == TaskStreamConnection.offline) ...[
              const Align(alignment: Alignment.centerLeft, child: LiveFeedNotice()),
              const SizedBox(height: PaiSpacing.sm),
            ],
            _statusHeader(),
            const SizedBox(height: 20),
            _section('Overview', [
              _kv('Task ID', _task.id),
              _kv('Type', _task.type),
              _kv('Status', _task.status.name),
              _kv('Priority', _task.priority.name),
              _kv('Source', _task.source.name),
              if ((_task.parentTaskId ?? '').isNotEmpty)
                _kv('Parent', _task.parentTaskId!),
              if (_task.childTaskIds.isNotEmpty)
                _kv('Children', _task.childTaskIds.join(', ')),
            ]),
            const SizedBox(height: 16),
            _section('Execution', [
              _kv('Capability', _task.capability ?? '—'),
              _kv('Plugin', _task.plugin ?? '—'),
              _kv('Device', _task.deviceId ?? '—'),
              _kv('Executor', _task.executorId ?? '—'),
              _kv('Retries', '${_task.retryCount} / ${_task.maxRetries}'),
              _kv('Timeout', '${_task.timeout}s'),
            ]),
            const SizedBox(height: 16),
            _section('Timing', [
              _kv('Created', _fmt(_task.createdAt)),
              if (_task.initializedAt != null)
                _kv('Initialized', _fmt(_task.initializedAt)),
              if (_task.queuedAt != null)
                _kv('Queued', _fmt(_task.queuedAt)),
              if (_task.startedAt != null)
                _kv('Started', _fmt(_task.startedAt)),
              if (_task.completedAt != null)
                _kv('Completed', _fmt(_task.completedAt)),
              if (_task.startedAt != null && _task.completedAt != null)
                _kv(
                  'Duration',
                  _dur(_task.completedAt!.difference(_task.startedAt!)),
                ),
            ]),
            if (_task.input.isNotEmpty) ...[
              const SizedBox(height: 16),
              _section('Input', [
                _codeBlock(_task.input, mono: false),
              ]),
            ],
            if (_task.parameters.isNotEmpty) ...[
              const SizedBox(height: 16),
              _jsonSection('Parameters', _task.parameters),
            ],
            if (_task.result != null) ...[
              const SizedBox(height: 16),
              _resultSection(_task.result),
            ],
            if ((_task.error ?? '').isNotEmpty) ...[
              const SizedBox(height: 16),
              _errorBox(_task.error!),
            ],
            if (_task.metadata.isNotEmpty) ...[
              const SizedBox(height: 16),
              _jsonSection('Metadata', _task.metadata),
            ],
            const SizedBox(height: 32),
          ],
            ),
          ),
        ),
      ),
    );
  }

  // ------------------------------------------------------------------
  // Header
  // ------------------------------------------------------------------

  Widget _statusHeader() {
    final hud = HudStatusX.fromTaskStatus(_task.status.name);
    final color = hud.color;
    final text = Theme.of(context).textTheme;
    return HudPanel(
      accent: color,
      selected: hud == HudStatus.running || hud == HudStatus.failed,
      padding: const EdgeInsets.all(PaiSpacing.lg),
      semanticLabel: 'Task status ${_task.status.name}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(_statusIcon(_task.status), color: color, size: 34),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      (_task.isRetrying ? _task.retryLabel : _task.status.name).toUpperCase(),
                      style: TextStyle(
                        fontFamily: PaiFonts.display,
                        color: color,
                        fontWeight: FontWeight.w700,
                        fontSize: 18,
                        letterSpacing: 3,
                      ),
                    ),
                    if (_task.capability != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text(_task.capability!, style: context.pai.mono(size: 12.5)),
                      ),
                  ],
                ),
              ),
              if (_loading)
                const SizedBox(
                  width: 20, height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
            ],
          ),
          const SizedBox(height: PaiSpacing.lg),
          _Timeline(task: _task),
          if (_task.status.name == 'running') ...[
            const SizedBox(height: PaiSpacing.md),
            const LinearProgressIndicator(minHeight: 2),
          ],
          if (_task.isRetrying && _task.retry != null) ...[
            const SizedBox(height: PaiSpacing.md),
            RetryCountdown(retry: _task.retry!),
          ],
          if (_task.cancelAbandoned) ...[
            const SizedBox(height: PaiSpacing.md),
            const AbandonedCancelNote(),
          ],
          if (_task.pauseBlocked && (_task.controlBlockedReason ?? '').isNotEmpty) ...[
            const SizedBox(height: PaiSpacing.md),
            Text(
              'Pause isn\'t available: ${_task.controlBlockedReason}',
              key: const Key('pause-blocked-note'),
              style: text.bodySmall?.copyWith(color: context.pai.muted),
            ),
          ],
          if (_task.retryCount > 0) ...[
            const SizedBox(height: PaiSpacing.md),
            Text('RETRY ${_task.retryCount} OF ${_task.maxRetries}',
                style: text.labelSmall?.copyWith(color: PaiColors.amber, letterSpacing: 2)),
          ],
        ],
      ),
    );
  }

  // ------------------------------------------------------------------
  // Sections
  // ------------------------------------------------------------------

  Widget _section(String title, List<Widget> children) {
    return HudPanel(
      title: title,
      padding: const EdgeInsets.all(PaiSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: children,
      ),
    );
  }

  Widget _kv(String key, String value) {
    final text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 104,
            child: Text(
              key.toUpperCase(),
              style: text.labelSmall?.copyWith(letterSpacing: 1.4),
            ),
          ),
          Expanded(
            child: SelectableText(
              value,
              style: text.bodyMedium,
            ),
          ),
        ],
      ),
    );
  }

  Widget _jsonSection(String title, Map<String, dynamic> data) {
    return _section(title, [
      _codeBlock(const JsonEncoder.withIndent('  ').convert(data)),
    ]);
  }

  Widget _codeBlock(String text, {bool mono = true}) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(PaiSpacing.md),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.28),
        borderRadius: BorderRadius.circular(PaiRadii.sm),
        border: Border.all(color: context.pai.line),
      ),
      child: SelectableText(
        text,
        style: mono
            ? context.pai.mono(size: 12, strong: true)
            : Theme.of(context).textTheme.bodyMedium,
      ),
    );
  }

  Widget _resultSection(dynamic result) {
    // TaskResult may be a class with toJson() or already a Map.
    // Either way, normalize to a Map for display.
    Map<String, dynamic> data;
    try {
      if (result is Map) {
        data = Map<String, dynamic>.from(result);
      } else {
        final dynamic r = result;
        final json = r.toJson();
        data = json is Map
            ? Map<String, dynamic>.from(json)
            : {'value': json};
      }
    } catch (_) {
      data = {'raw': result.toString()};
    }

    final ok = data['success'] == true;
    final error = data['error']?.toString();

    return _section('Result', [
      Row(
        children: [
          Icon(
            ok ? Icons.check_circle : Icons.error,
            color: ok ? PaiColors.green : PaiColors.red,
            size: 18,
          ),
          const SizedBox(width: 6),
          Text(
            ok ? 'Success' : 'Failed',
            style: Theme.of(context).textTheme.labelLarge?.copyWith(
                  color: ok ? PaiColors.green : PaiColors.red,
                ),
          ),
        ],
      ),
      if (error != null && error.isNotEmpty) ...[
        const SizedBox(height: 8),
        SelectableText(
          error,
          style: Theme.of(context).textTheme.bodySmall?.copyWith(color: PaiColors.red),
        ),
      ],
      const SizedBox(height: 8),
      _codeBlock(const JsonEncoder.withIndent('  ').convert(data)),
    ]);
  }

  Widget _errorBox(String error) {
    return HudPanel(
      title: 'Error',
      leadingIcon: Icons.warning_amber_rounded,
      accent: PaiColors.red,
      padding: const EdgeInsets.all(PaiSpacing.md),
      child: SelectableText(
        error,
        style: context.pai.mono(size: 12.5, strong: true),
      ),
    );
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  IconData _statusIcon(TaskStatus status) {
    switch (status.name) {
      case 'completed': return Icons.check_circle;
      case 'failed':    return Icons.error;
      case 'cancelled': return Icons.cancel;
      case 'running':   return Icons.play_circle;
      case 'paused':    return Icons.pause_circle;
      case 'queued':    return Icons.schedule;
      case 'retrying':  return Icons.refresh;
      default:          return Icons.circle_outlined;
    }
  }

  String _fmt(DateTime? dt) {
    if (dt == null) return '—';
    return dt.toLocal().toString().split('.').first;
  }

  String _dur(Duration d) {
    if (d.inHours > 0) {
      return '${d.inHours}h ${d.inMinutes.remainder(60)}m '
             '${d.inSeconds.remainder(60)}s';
    }
    if (d.inMinutes > 0) {
      return '${d.inMinutes}m ${d.inSeconds.remainder(60)}s';
    }
    if (d.inSeconds > 0) {
      return '${d.inSeconds}s ${d.inMilliseconds.remainder(1000)}ms';
    }
    return '${d.inMilliseconds}ms';
  }
}


/// Created → Queued → Started → Finished, from the task's timestamps.
class _Timeline extends StatelessWidget {
  const _Timeline({required this.task});
  final Task task;

  @override
  Widget build(BuildContext context) {
    final failed = task.status.name == 'failed';
    final cancelled = task.status.name == 'cancelled';
    final end = task.completedAt;
    final steps = <(String, DateTime?, Color)>[
      ('Created', task.createdAt, PaiColors.cyan),
      ('Queued', task.queuedAt, PaiColors.azure),
      ('Started', task.startedAt, PaiColors.cyan),
      (
        failed ? 'Failed' : cancelled ? 'Cancelled' : 'Finished',
        end,
        failed ? PaiColors.red : cancelled ? context.pai.faint : PaiColors.green,
      ),
    ];
    final text = Theme.of(context).textTheme;
    return Semantics(
      label: 'Timeline: ${steps.where((s) => s.$2 != null).map((s) => s.$1).join(', ')}',
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (var i = 0; i < steps.length; i++) ...[
            Expanded(
              child: Column(
                children: [
                  Row(
                    children: [
                      Expanded(child: i == 0 ? const SizedBox() : _line(steps[i].$2 != null, steps[i].$3, context.pai.line)),
                      Container(
                        width: 12,
                        height: 12,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: steps[i].$2 != null ? steps[i].$3 : Colors.transparent,
                          border: Border.all(
                              color: steps[i].$2 != null ? steps[i].$3 : context.pai.line, width: 1.6),
                          boxShadow: steps[i].$2 != null
                              ? [BoxShadow(color: steps[i].$3.withValues(alpha: 0.5), blurRadius: 8)]
                              : null,
                        ),
                      ),
                      Expanded(child: i == steps.length - 1 ? const SizedBox() : _line(steps[i + 1].$2 != null, steps[i + 1].$3, context.pai.line)),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(steps[i].$1.toUpperCase(),
                      textAlign: TextAlign.center,
                      style: text.labelSmall?.copyWith(
                          letterSpacing: 1.2,
                          color: steps[i].$2 != null ? context.pai.strong : context.pai.faint)),
                  if (steps[i].$2 != null)
                    Text(_hm(steps[i].$2!), style: PaiText.mono(size: 10.5, color: context.pai.muted)),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  static Widget _line(bool reached, Color color, Color idle) => Container(
        height: 2,
        color: reached ? color.withValues(alpha: 0.7) : idle,
      );

  static String _hm(DateTime t) {
    final l = t.toLocal();
    String two(int v) => v.toString().padLeft(2, '0');
    return '${two(l.hour)}:${two(l.minute)}:${two(l.second)}';
  }
}
