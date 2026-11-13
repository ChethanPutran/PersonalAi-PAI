import 'dart:async';

import 'package:flutter/material.dart';
import 'package:pai_mobile/protocol/device_enums.dart';
import 'package:pai_mobile/ui/tasks/task_detail_screen.dart';

import '../../../tasks/task.dart';
import '../../../tasks/task_service.dart';


class TasksScreen extends StatefulWidget {
  final TaskService service;

  /// Optional live feed from [DeviceConnectionService.taskEvents].
  /// When provided, the screen subscribes and refreshes on every event.
  final Stream<Map<String, dynamic>>? taskEvents;

  const TasksScreen({
    super.key,
    required this.service,
    this.taskEvents,
  });


  @override
  State<TasksScreen> createState() =>
      _TasksScreenState();
}

// lib/ui/tasks/tasks_screen.dart — replace _TasksScreenState

class _TasksScreenState extends State<TasksScreen> {
  List<Task> tasks = [];

  StreamSubscription<Map<String, dynamic>>? _taskEventSub;
  Timer? _pollTimer;

  bool _loading = false;
  bool _loadedOnce = false;      // distinguish "empty" from "never loaded"
  String? _error;                // surface load failures to the user

@override
void initState() {
  super.initState();
  load();

  final events = widget.taskEvents;
  var gotEvent = false;

  if (events != null) {
    _taskEventSub = events.listen((msg) {
      final type = DeviceMessageType.fromWire(msg['type']?.toString());
      if (type == null || !type.isTaskEvent) return;
      gotEvent = true;
      load();
    });
  }

  // Poll as a fallback *regardless* of whether we have a stream —
  // the stream may be empty if the backend-side subscriber is
  // missing or the device is offline. If an event arrived within
  // the last interval, skip the poll to avoid redundant fetches.
  _pollTimer = Timer.periodic(const Duration(seconds: 5), (_) {
    if (gotEvent) {
      gotEvent = false;    // one event is enough to satisfy this tick
      return;
    }
    load();
  });
}
  @override
  void dispose() {
    _taskEventSub?.cancel();
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> load() async {
    if (_loading) return;
    _loading = true;
    setState(() => _error = null);

    try {
      final result = await widget.service.getTasks();
      if (!mounted) return;
      setState(() {
        tasks = result;
        _loadedOnce = true;
        _error = null;
      });
    } catch (e) {
      debugPrint('[TasksScreen] load failed: $e');
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loadedOnce = true;
      });
    } finally {
      _loading = false;
    }
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
                  child: CircularProgressIndicator(strokeWidth: 2),
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
    // ── 1. First load still in flight ──
    if (!_loadedOnce && _loading) {
      return const Center(child: CircularProgressIndicator());
    }

    // ── 2. Load failed (and no cached data to show) ──
    if (_error != null && tasks.isEmpty) {
      return ListView(
        // ListView, not Center, so RefreshIndicator has a scrollable
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
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          Text(
            _error!,
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 12, color: Colors.grey.shade700),
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

    // ── 3. Loaded, no tasks ──
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
            style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
          ),
        ],
      );
    }

    // ── 4. Loaded, tasks present ──
    return ListView.builder(
      itemCount: tasks.length,
      itemBuilder: (_, index) {
        final task = tasks[index];
        return ListTile(
          title: Text(task.title),
          subtitle: Text(task.status.name),
          onTap: () async {
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
          },
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (task.status == TaskStatus.running)
                IconButton(
                  icon: const Icon(Icons.pause),
                  onPressed: () async {
                    try {
                      await widget.service.pause(task.id);
                      await load();
                    } catch (e) {
                      if (!mounted) return;
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text('Pause failed: $e')),
                      );
                    }
                  },
                ),
              if (task.status == TaskStatus.paused)
                IconButton(
                  icon: const Icon(Icons.play_arrow),
                  onPressed: () async {
                    try {
                      await widget.service.resume(task.id);
                      await load();
                    } catch (e) {
                      if (!mounted) return;
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text('Resume failed: $e')),
                      );
                    }
                  },
                ),
              if (task.status == TaskStatus.running ||
                  task.status == TaskStatus.paused)
                IconButton(
                  icon: const Icon(Icons.stop),
                  onPressed: () async {
                    try {
                      await widget.service.cancel(task.id);
                      await load();
                    } catch (e) {
                      if (!mounted) return;
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text('Cancel failed: $e')),
                      );
                    }
                  },
                ),
            ],
          ),
        );
      },
    );
  }
}