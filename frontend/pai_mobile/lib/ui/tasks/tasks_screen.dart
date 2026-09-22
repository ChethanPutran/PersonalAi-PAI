import 'package:flutter/material.dart';

import '../../../tasks/task.dart';
import '../../../tasks/task_service.dart';


class TasksScreen extends StatefulWidget {
  final TaskService service;

  const TasksScreen({
    super.key,
    required this.service,
  });


  @override
  State<TasksScreen> createState() =>
      _TasksScreenState();
}


class _TasksScreenState
    extends State<TasksScreen> {

  List<Task> tasks = [];


  @override
  void initState() {
    super.initState();
    load();
  }


  Future<void> load() async {
    final result =
        await widget.service.getTasks();

    setState(() {
      tasks = result;
    });
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
                        await widget.service
                            .pause(task.id);
                        await load();
                      },
                    ),

                  if (task.status ==
                      TaskStatus.paused)
                    IconButton(
                      icon: const Icon(
                        Icons.play_arrow,
                      ),
                      onPressed: () async {
                        await widget.service
                            .resume(task.id);
                        await load();
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
                        await widget.service
                            .cancel(task.id);
                        await load();
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