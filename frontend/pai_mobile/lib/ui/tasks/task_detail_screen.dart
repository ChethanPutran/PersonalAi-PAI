import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import '../../../tasks/task.dart';
import '../../../tasks/task_service.dart';


class TaskDetailScreen extends StatefulWidget {
  final Task initialTask;
  final TaskService service;

  /// Optional live feed from [DeviceConnectionService.taskEvents].
  /// When provided, refreshes whenever a task/plan event arrives.
  final Stream<Map<String, dynamic>>? taskEvents;

  const TaskDetailScreen({
    super.key,
    required this.initialTask,
    required this.service,
    this.taskEvents,
  });

  @override
  State<TaskDetailScreen> createState() => _TaskDetailScreenState();
}


class _TaskDetailScreenState extends State<TaskDetailScreen> {
  late Task _task;

  StreamSubscription<Map<String, dynamic>>? _taskEventSub;
  Timer? _pollTimer;
  bool _loading = false;
  bool _busy = false;   // prevents double-tapping pause/resume/cancel

  @override
  void initState() {
    super.initState();
    _task = widget.initialTask;
    _setupAutoRefresh();
  }

  void _setupAutoRefresh() {
    // WS events are the preferred refresh signal.
    final events = widget.taskEvents;
    if (events != null) {
      _taskEventSub = events.listen((msg) {
        final taskId = msg['task_id']?.toString();
        // taskId == null → plan-level event, refresh anyway.
        if (taskId == null || taskId == _task.id) {
          _refresh();
        }
      });
    }

    // 2-second poll as a belt-and-suspenders fallback while the task
    // is non-terminal. Cancels itself once the task finishes.
    _pollTimer = Timer.periodic(
      const Duration(seconds: 2),
      (_) {
        if (_isTerminalStatus(_task.status)) {
          _pollTimer?.cancel();
          _pollTimer = null;
          return;
        }
        _refresh();
      },
    );
  }

  @override
  void dispose() {
    _taskEventSub?.cancel();
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    if (_loading) return;
    _loading = true;
    try {
      final updated = await widget.service.getTask(_task.id);
      if (!mounted) return;
      setState(() => _task = updated);
      if (_isTerminalStatus(_task.status)) {
        _pollTimer?.cancel();
        _pollTimer = null;
      }
    } catch (e) {
      debugPrint('[TaskDetail] refresh failed: $e');
    } finally {
      _loading = false;
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
      await _refresh();
    } catch (e) {
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
    final status = _task.status;
    final running = status.name == 'running';
    final paused = status.name == 'paused';
    final terminal = _isTerminalStatus(status);

    return Scaffold(
      appBar: AppBar(
        title: Text(_task.title, overflow: TextOverflow.ellipsis),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            icon: const Icon(Icons.refresh),
            onPressed: _loading ? null : _refresh,
          ),
          if (running)
            IconButton(
              tooltip: 'Pause',
              icon: const Icon(Icons.pause),
              onPressed: _busy
                  ? null
                  : () => _action('Pause', widget.service.pause),
            ),
          if (paused)
            IconButton(
              tooltip: 'Resume',
              icon: const Icon(Icons.play_arrow),
              onPressed: _busy
                  ? null
                  : () => _action('Resume', widget.service.resume),
            ),
          if (!terminal)
            IconButton(
              tooltip: 'Cancel',
              icon: const Icon(Icons.stop),
              onPressed: _busy
                  ? null
                  : () => _action('Cancel', widget.service.cancel),
            ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
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
    );
  }

  // ------------------------------------------------------------------
  // Header
  // ------------------------------------------------------------------

  Widget _statusHeader() {
    final color = _statusColor(_task.status);
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: color.withOpacity(0.08),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withOpacity(0.35)),
      ),
      child: Row(
        children: [
          Icon(_statusIcon(_task.status), color: color, size: 32),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _task.status.name.toUpperCase(),
                  style: TextStyle(
                    color: color,
                    fontWeight: FontWeight.bold,
                    fontSize: 15,
                    letterSpacing: 0.6,
                  ),
                ),
                if (_task.capability != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      _task.capability!,
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 12,
                      ),
                    ),
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
    );
  }

  // ------------------------------------------------------------------
  // Sections
  // ------------------------------------------------------------------

  Widget _section(String title, List<Widget> children) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(bottom: 6, left: 4),
          child: Text(
            title.toUpperCase(),
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.bold,
              color: Colors.grey.shade600,
              letterSpacing: 0.6,
            ),
          ),
        ),
        Card(
          margin: EdgeInsets.zero,
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: children,
            ),
          ),
        ),
      ],
    );
  }

  Widget _kv(String key, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 100,
            child: Text(
              key,
              style: TextStyle(
                color: Colors.grey.shade600,
                fontSize: 12,
              ),
            ),
          ),
          Expanded(
            child: SelectableText(
              value,
              style: const TextStyle(fontSize: 13),
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
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Colors.grey.shade100,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: Colors.grey.shade300),
      ),
      child: SelectableText(
        text,
        style: TextStyle(
          fontFamily: mono ? 'monospace' : null,
          fontSize: 12,
        ),
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
            color: ok ? Colors.green : Colors.red,
            size: 18,
          ),
          const SizedBox(width: 6),
          Text(
            ok ? 'Success' : 'Failed',
            style: TextStyle(
              color: ok ? Colors.green : Colors.red,
              fontWeight: FontWeight.bold,
            ),
          ),
        ],
      ),
      if (error != null && error.isNotEmpty) ...[
        const SizedBox(height: 8),
        SelectableText(
          error,
          style: const TextStyle(color: Colors.red, fontSize: 12),
        ),
      ],
      const SizedBox(height: 8),
      _codeBlock(const JsonEncoder.withIndent('  ').convert(data)),
    ]);
  }

  Widget _errorBox(String error) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.red.shade50,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.red.shade200),
      ),
      child: SelectableText(
        error,
        style: const TextStyle(color: Colors.red, fontSize: 12),
      ),
    );
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  static bool _isTerminalStatus(TaskStatus s) {
    // Compare by name so this works regardless of which statuses the
    // Dart enum actually declares.
    final n = s.name;
    return n == 'completed' || n == 'failed' || n == 'cancelled';
  }

  Color _statusColor(TaskStatus status) {
    switch (status.name) {
      case 'completed': return Colors.green;
      case 'failed':    return Colors.red;
      case 'cancelled': return Colors.grey;
      case 'running':   return Colors.blue;
      case 'paused':    return Colors.orange;
      case 'queued':    return Colors.purple;
      case 'retrying':  return Colors.amber;
      default:          return Colors.grey;
    }
  }

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