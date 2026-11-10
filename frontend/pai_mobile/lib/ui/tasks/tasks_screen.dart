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


class _TasksScreenState
    extends State<TasksScreen> {

  List<Task> tasks = [];

  StreamSubscription<Map<String, dynamic>>? _taskEventSub;
  Timer? _pollTimer;

  bool _loading = false;


  @override
  void initState() {
    super.initState();
    load();

    // WS task events → refresh (preferred)
    final events = widget.taskEvents;
    if (events != null) {
      _taskEventSub = events.listen((msg) {
        // Only refresh on typed task/plan events — anything else is
        // ignored. Using the enum means a typo like 'task.complted'
        // (or a renamed event on the backend) silently no-ops instead
        // of showing stale state.
        final type = DeviceMessageType.fromWire(msg['type']?.toString());
        if (type == null || !type.isTaskEvent) return;
        load();
      });
    } else {
      // Fallback: poll every 5s so backend-created tasks show up even
      // if the backend hasn't wired task events over WS yet.
      _pollTimer = Timer.periodic(
        const Duration(seconds: 5),
        (_) => load(),
      );
    }
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
    try {
      final result = await widget.service.getTasks();
      if (!mounted) return;
      setState(() {
        tasks = result;
      });
    } catch (e) {
      debugPrint('[TasksScreen] load failed: $e');
    } finally {
      _loading = false;
    }
  }


  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Tasks'),
      ),

      body: RefreshIndicator(
        onRefresh: load,

        child: ListView.builder(
          itemCount: tasks.length,

          itemBuilder: (_, index) {
            final task = tasks[index];

            return ListTile(
              title: Text(task.title),

              subtitle: Text(
                task.status.name,
              ),
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
                  if (task.status ==
                      TaskStatus.running)
                    IconButton(
                      icon: const Icon(
                        Icons.pause,
                      ),
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

                  if (task.status ==
                      TaskStatus.paused)
                    IconButton(
                      icon: const Icon(
                        Icons.play_arrow,
                      ),
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

                  if (task.status ==
                          TaskStatus.running ||
                      task.status ==
                          TaskStatus.paused)
                    IconButton(
                      icon: const Icon(
                        Icons.stop,
                      ),
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
        ),
      ),
    );
  }
}