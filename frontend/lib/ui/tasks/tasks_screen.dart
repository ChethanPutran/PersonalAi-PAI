
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:pai_mobile/protocol/device_enums.dart';
import 'package:pai_mobile/ui/tasks/task_detail_screen.dart';

import '../../../tasks/task.dart';
import '../../../tasks/task_service.dart';

class TasksScreen extends StatefulWidget {
  final TaskService service;

  /// Live feed of task events received through the device WebSocket.
  final Stream<Map<String, dynamic>>? taskEvents;

  const TasksScreen({
    super.key,
    required this.service,
    this.taskEvents,
  });

  @override
  State<TasksScreen> createState() => _TasksScreenState();
}

class _TasksScreenState extends State<TasksScreen> {
  List<Task> tasks = [];

  StreamSubscription<Map<String, dynamic>>? _taskEventSub;

  bool _loading = false;
  bool _refreshPending = false;
  bool _loadedOnce = false;

  String? _error;

  @override
  void initState() {
    super.initState();

    // Initial synchronization.
    load();

    // Subscribe to WebSocket task events.
    final events = widget.taskEvents;

    if (events != null) {
      _taskEventSub = events.listen(
        (msg) {
          final type = DeviceMessageType.fromWire(
            msg['type']?.toString(),
          );

          if (type == null || !type.isTaskEvent) return;

          debugPrint(
            '[TasksScreen] Task event received: ${msg['type']}',
          );

          load();
        },
        onError: (error) {
          debugPrint('[TasksScreen] Task event stream error: $error');
        },
      );
    }
  }

  @override
  void dispose() {
    _taskEventSub?.cancel();
    super.dispose();
  }

  /// Fetches tasks from the backend.
  ///
  /// If another refresh is already running, queues one additional refresh
  /// instead of silently dropping the request.
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

        final result = await widget.service.getTasks();

        if (!mounted) return;

        setState(() {
          tasks = result;
          _loadedOnce = true;
          _error = null;
        });
      } while (_refreshPending && mounted);
    } catch (e) {
      debugPrint('[TasksScreen] Load failed: $e');

      if (!mounted) return;

      setState(() {
        _error = e.toString();
        _loadedOnce = true;
      });
    } finally {
      _loading = false;
    }
  }

  Future<void> _pauseTask(Task task) async {
    try {
      await widget.service.pause(task.id);
      await load();
    } catch (e) {
      _showError('Pause failed: $e');
    }
  }

  Future<void> _resumeTask(Task task) async {
    try {
      await widget.service.resume(task.id);
      await load();
    } catch (e) {
      _showError('Resume failed: $e');
    }
  }

  Future<void> _cancelTask(Task task) async {
    try {
      await widget.service.cancel(task.id);
      await load();
    } catch (e) {
      _showError('Cancel failed: $e');
    }
  }

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
          taskEvents: widget.taskEvents,
        ),
      ),
    );

    if (!mounted) return;

    await load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Tasks'),
        actions: [
          if (_loading)
            const Padding(
              padding: EdgeInsets.only(right: 16),
              child: Center(
                child: SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                  ),
                ),
              ),
            ),
          IconButton(
            tooltip: 'Refresh',
            icon: const Icon(Icons.refresh),
            onPressed: _loading ? null : load,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: load,
        child: _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    // Initial loading.
    if (!_loadedOnce && _loading) {
      return const Center(
        child: CircularProgressIndicator(),
      );
    }

    // Loading failed and there is no cached data.
    if (_error != null && tasks.isEmpty) {
      return ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(24),
        children: [
          Icon(
            Icons.error_outline,
            size: 48,
            color: Colors.red.shade400,
          ),
          const SizedBox(height: 12),
          const Text(
            'Failed to load tasks',
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            _error!,
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 12,
              color: Colors.grey.shade700,
            ),
          ),
          const SizedBox(height: 16),
          Center(
            child: ElevatedButton.icon(
              icon: const Icon(Icons.refresh),
              label: const Text('Retry'),
              onPressed: load,
            ),
          ),
        ],
      );
    }

    // No tasks.
    if (tasks.isEmpty) {
      return ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(24),
        children: [
          const SizedBox(height: 100),
          Icon(
            Icons.task_alt,
            size: 48,
            color: Colors.grey.shade400,
          ),
          const SizedBox(height: 12),
          Text(
            'No tasks yet',
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w500,
              color: Colors.grey.shade700,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            'Ask PAI to do something with a capability '
            '(e.g. "take a photo") and the task will appear here.',
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 12,
              color: Colors.grey.shade600,
            ),
          ),
        ],
      );
    }

    // Tasks available.
    return ListView.builder(
      physics: const AlwaysScrollableScrollPhysics(),
      itemCount: tasks.length,
      itemBuilder: (_, index) {
        final task = tasks[index];

        return ListTile(
          title: Text(task.title),
          subtitle: Text(task.status.name),
          onTap: () => _openTask(task),
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (task.status == TaskStatus.running)
                IconButton(
                  tooltip: 'Pause',
                  icon: const Icon(Icons.pause),
                  onPressed: () => _pauseTask(task),
                ),

              if (task.status == TaskStatus.paused)
                IconButton(
                  tooltip: 'Resume',
                  icon: const Icon(Icons.play_arrow),
                  onPressed: () => _resumeTask(task),
                ),

              if (task.status == TaskStatus.running ||
                  task.status == TaskStatus.paused)
                IconButton(
                  tooltip: 'Cancel',
                  icon: const Icon(Icons.stop),
                  onPressed: () => _cancelTask(task),
                ),
            ],
          ),
        );
      },
    );
  }
}
