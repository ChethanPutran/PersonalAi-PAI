
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:pai_mobile/ui/tasks/task_detail_screen.dart';

import '../../../tasks/task.dart';
import '../../../tasks/task_event_stream.dart';
import '../../../tasks/task_events.dart';
import '../../../tasks/task_service.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';
import 'live_feed_notice.dart';
import 'retry_countdown.dart';

/// The user's tasks, kept current by a server push — there is NO polling.
///
/// One `GET /tasks` on open; after that every change arrives on [live] as a full task snapshot
/// and is upserted into the list (a task created while the screen is open — e.g. a scheduled
/// plugin run — just appears). If the server says it could not replay what we missed
/// ([TaskStreamResync]) the list reloads once.
class TasksScreen extends StatefulWidget {
  final TaskService service;

  /// The live feed (see [TaskEventStream]). Null = no push (the screen still works with
  /// pull-to-refresh and refreshes itself after the actions it performs).
  final TaskLiveFeed? live;

  const TasksScreen({
    super.key,
    required this.service,
    this.live,
  });

  @override
  State<TasksScreen> createState() => _TasksScreenState();
}

class _TasksScreenState extends State<TasksScreen> {
  List<Task> tasks = [];

  StreamSubscription<TaskStreamEvent>? _eventSub;
  StreamSubscription<TaskStreamConnection>? _connSub;
  TaskStreamConnection _conn = TaskStreamConnection.connected;

  bool _loading = false;
  bool _refreshPending = false;
  bool _loadedOnce = false;

  /// Events that arrive while a GET is in flight. The GET snapshot may predate them, so they
  /// are re-applied on top of it when it lands.
  final List<Task> _duringLoad = [];

  String? _error;

  @override
  void initState() {
    super.initState();

    final live = widget.live;
    if (live != null) {
      _conn = live.state;
      _eventSub = live.events.listen(_onLiveEvent, onError: (Object e) {
        debugPrint('[TasksScreen] live feed error: $e');
      });
      _connSub = live.connection.listen((c) {
        if (mounted && c != _conn) setState(() => _conn = c);
      });
    }

    // The one initial synchronization.
    load();
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    _connSub?.cancel();
    super.dispose();
  }

  void _onLiveEvent(TaskStreamEvent event) {
    switch (event) {
      case TaskUpdated(:final task, :final seq, event: final name):
        debugPrint('[TasksScreen] $name #$seq ${task.id} → ${task.status.name}');
        if (_loading) _duringLoad.add(task);
        if (mounted) setState(() => tasks = upsertTask(tasks, task));
      case TaskStreamResync(:final reason):
        debugPrint('[TasksScreen] resync ($reason) → reloading once');
        load();
      case TaskStreamReady():
        break;
    }
  }

  /// True when nothing will tell us about the result of an action (no feed, or it is down), so
  /// the screen refreshes itself once instead of leaving stale state.
  bool get _needsManualRefresh =>
      widget.live == null || widget.live!.state != TaskStreamConnection.connected;

  /// Fetches the full list. Concurrent calls coalesce into one extra refresh.
  Future<void> load() async {
    if (_loading) {
      _refreshPending = true;
      return;
    }

    _loading = true;

    if (mounted) {
      setState(() {
        _error = null;
      });
    }

    try {
      do {
        _refreshPending = false;
        _duringLoad.clear();

        final result = await widget.service.getTasks();

        if (!mounted) return;

        var merged = sortTasks(result);
        for (final t in _duringLoad) {
          merged = upsertTask(merged, t);
        }
        _duringLoad.clear();

        setState(() {
          tasks = merged;
          _loadedOnce = true;
          _error = null;
        });
      } while (_refreshPending && mounted);
    } catch (e, st) {
      debugPrint('[TasksScreen] Load failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[TasksScreen] load', maxFrames: 10);

      if (!mounted) return;

      setState(() {
        _error = e.toString();
        _loadedOnce = true;
      });
    } finally {
      _loading = false;
    }
  }

  Future<void> _control(String label, Future<void> Function(String) op, Task task) async {
    try {
      await op(task.id);
      // Never optimistic: the confirming event updates the card. Only when no event can arrive
      // (no feed / feed down) do we fetch once.
      if (_needsManualRefresh) await load();
    } on TaskControlException catch (e) {
      debugPrint('[TasksScreen] $label refused (${e.code}): ${e.message}');
      _showError(e.message);
    } catch (e, st) {
      debugPrint('[TasksScreen] $label failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[TasksScreen] $label', maxFrames: 8);
      _showError('$label failed: $e');
    }
  }

  Future<void> _pauseTask(Task task) => _control('Pause', widget.service.pause, task);

  Future<void> _resumeTask(Task task) => _control('Resume', widget.service.resume, task);

  Future<void> _cancelTask(Task task) => _control('Cancel', widget.service.cancel, task);

  void _showError(String message) {
    if (!mounted) return;

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  Future<void> _openTask(Task task) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => TaskDetailScreen(
          initialTask: task,
          service: widget.service,
          live: widget.live,
        ),
      ),
    );
    // No refresh on return: the list received the same events while the detail was open.
    // Without a feed there is nothing pushing, so fetch once.
    if (mounted && widget.live == null) await load();
  }

  @override
  Widget build(BuildContext context) {
    // The shell already provides the top bar / navigation; this screen adds its own header
    // row with the refresh control so it also works standalone (tests, deep links).
    return HudScaffold(
      appBar: HudAppBar(
        title: 'Tasks',
        automaticallyImplyLeading: false,
        actions: [
          if (_loading)
            const Padding(
              padding: EdgeInsets.only(right: 8),
              child: Center(
                child: SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ),
            ),
          HudIconButton(
            tooltip: 'Refresh',
            icon: Icons.refresh,
            onPressed: _loading ? null : load,
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: Column(
        children: [
          if (_conn == TaskStreamConnection.offline) const LiveFeedNotice(),
          Expanded(
            child: RefreshIndicator(
              onRefresh: load,
              child: _buildBody(),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBody() {
    // Initial loading.
    if (!_loadedOnce && _loading) {
      return const HudSkeletonList();
    }

    // Loading failed and there is no cached data.
    if (_error != null && tasks.isEmpty) {
      return LayoutBuilder(
        builder: (context, box) => ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: [
            SizedBox(
              height: box.maxHeight,
              child: HudErrorState(
                title: 'Failed to load tasks',
                message: 'The task list could not be retrieved from the server.',
                details: _error!,
                onRetry: load,
              ),
            ),
          ],
        ),
      );
    }

    // No tasks.
    if (tasks.isEmpty) {
      return LayoutBuilder(
        builder: (context, box) => ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: [
            SizedBox(
              height: box.maxHeight,
              child: const HudEmptyState(
                icon: Icons.task_alt,
                title: 'No tasks yet',
                message: 'Ask PAI to do something with a capability '
                    '(e.g. "take a photo") and the task will appear here.',
              ),
            ),
          ],
        ),
      );
    }

    // Tasks available.
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 860),
        child: ListView.separated(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(PaiSpacing.lg),
          itemCount: tasks.length,
          separatorBuilder: (_, _) => const SizedBox(height: PaiSpacing.md),
          itemBuilder: (_, index) {
            final task = tasks[index];
            return TaskCard(
              task: task,
              onTap: () => _openTask(task),
              onPause: () => _pauseTask(task),
              onResume: () => _resumeTask(task),
              onCancel: () => _cancelTask(task),
            );
          },
        ),
      ),
    );
  }
}

/// One task as a HUD panel: status chip, title, capability/device, timing, failure reason,
/// and the pause / resume / cancel actions that apply to its state.
class TaskCard extends StatelessWidget {
  const TaskCard({
    super.key,
    required this.task,
    required this.onTap,
    required this.onPause,
    required this.onResume,
    required this.onCancel,
  });

  final Task task;
  final VoidCallback onTap;
  final VoidCallback onPause;
  final VoidCallback onResume;
  final VoidCallback onCancel;

  static String ago(DateTime t) {
    final d = PaiClock.now().difference(t);
    if (d.inSeconds < 60) return 'just now';
    if (d.inMinutes < 60) return '${d.inMinutes} min ago';
    if (d.inHours < 24) return '${d.inHours} h ago';
    return '${d.inDays} d ago';
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final status = HudStatusX.fromTaskStatus(task.status.name);
    final failed = task.status == TaskStatus.failed;
    // The server decides what this task's plugin can do (pause is offered only if supported).
    final hasActions = task.canPause || task.canResume || task.canCancel;
    final reduceMotion = MediaQuery.maybeOf(context)?.disableAnimations ?? false;
    final meta = [
      if (task.capability != null) task.capability!,
      if (task.deviceId != null) task.deviceId!,
    ].join('  ·  ');

    return HudPanel(
      onTap: onTap,
      accent: failed ? scheme.error : (status == HudStatus.running ? scheme.primary : null),
      selected: status == HudStatus.running,
      semanticLabel: 'Task ${task.title}, ${status.label}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              // Status changes arrive as live events: crossfade the chip instead of snapping.
              AnimatedSwitcher(
                duration: reduceMotion ? Duration.zero : const Duration(milliseconds: 220),
                child: StatusChip(
                  key: ValueKey(task.status),
                  status: status,
                  label: task.isRetrying ? task.retryLabel : task.status.name,
                  compact: true,
                ),
              ),
              const Spacer(),
              Text(ago(task.createdAt), style: context.pai.mono(size: 11, faint: true)),
            ],
          ),
          const SizedBox(height: PaiSpacing.sm),
          Text(task.title, maxLines: 2, overflow: TextOverflow.ellipsis, style: text.titleMedium),
          if (meta.isNotEmpty) ...[
            const SizedBox(height: 2),
            Text(meta, maxLines: 1, overflow: TextOverflow.ellipsis, style: context.pai.mono(size: 12)),
          ],
          if (failed) ...[
            const SizedBox(height: PaiSpacing.sm),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.error_outline, size: 16, color: scheme.error),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(taskFailureText(task),
                      key: const Key('task-failure-text'),
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: text.bodySmall?.copyWith(color: scheme.error)),
                ),
              ],
            ),
          ],
          if (task.isRetrying && task.retry != null) ...[
            const SizedBox(height: PaiSpacing.sm),
            RetryCountdown(retry: task.retry!),
          ],
          if (task.cancelAbandoned) ...[
            const SizedBox(height: PaiSpacing.sm),
            const AbandonedCancelNote(),
          ],
          if (task.status == TaskStatus.running) ...[
            const SizedBox(height: PaiSpacing.md),
            const LinearProgressIndicator(minHeight: 2),
          ],
          if (hasActions) ...[
            const SizedBox(height: PaiSpacing.sm),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                if (task.canPause)
                  HudIconButton(tooltip: 'Pause', icon: Icons.pause, onPressed: onPause),
                if (task.canResume)
                  HudIconButton(tooltip: 'Resume', icon: Icons.play_arrow, active: true, onPressed: onResume),
                if (task.canCancel) ...[
                  const SizedBox(width: PaiSpacing.sm),
                  HudIconButton(tooltip: 'Cancel', icon: Icons.stop, color: scheme.error, onPressed: onCancel),
                ],
              ],
            ),
          ],
        ],
      ),
    );
  }
}


/// Shown on a cancelled task whose device could not confirm it stopped.
class AbandonedCancelNote extends StatelessWidget {
  const AbandonedCancelNote({super.key});

  static const message = 'Stopped waiting — the device may have finished it';

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final amber = context.pai.amber;
    return Row(
      key: const Key('abandoned-cancel-note'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(Icons.info_outline, size: 16, color: amber),
        const SizedBox(width: 6),
        Expanded(child: Text(message, style: text.bodySmall?.copyWith(color: amber))),
      ],
    );
  }
}
