import 'package:flutter/material.dart';

import '../../models/schedule.dart';
import '../../schedules/schedule_service.dart';
import '../../tasks/task.dart';
import '../../tasks/task_service.dart';
import '../tasks/task_detail_screen.dart';

class ScheduleHistoryScreen extends StatefulWidget {
  final Schedule schedule;
  final ScheduleService scheduleService;
  final TaskService taskService;

  const ScheduleHistoryScreen({
    super.key,
    required this.schedule,
    required this.scheduleService,
    required this.taskService,
  });

  @override
  State<ScheduleHistoryScreen> createState() => _ScheduleHistoryScreenState();
}

class _ScheduleHistoryScreenState extends State<ScheduleHistoryScreen> {
  List<Task> _tasks = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final list = await widget.scheduleService.getHistory(widget.schedule.id);
      if (!mounted) return;
      setState(() {
        _tasks = list;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('History · ${widget.schedule.title}')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return ListView(
        padding: const EdgeInsets.all(24),
        children: [
          const Icon(Icons.error_outline, size: 48, color: Colors.red),
          const SizedBox(height: 16),
          Text('Failed to load history\n\n$_error', textAlign: TextAlign.center),
          const SizedBox(height: 24),
          Center(child: ElevatedButton(onPressed: _load, child: const Text('Retry'))),
        ],
      );
    }
    if (_tasks.isEmpty) {
      return ListView(
        padding: const EdgeInsets.all(32),
        children: const [
          SizedBox(height: 60),
          Icon(Icons.history, size: 64, color: Colors.grey),
          SizedBox(height: 16),
          Text(
            'No firings yet',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
          ),
          SizedBox(height: 8),
          Text(
            'Each time this reminder fires, its task will appear here.',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.grey),
          ),
        ],
      );
    }

    return ListView.separated(
      itemCount: _tasks.length,
      separatorBuilder: (_, __) => const Divider(height: 1),
      itemBuilder: (_, i) {
        final t = _tasks[i];
        final statusColor = switch (t.status) {
          TaskStatus.completed => Colors.green,
          TaskStatus.failed => Colors.red,
          TaskStatus.cancelled => Colors.orange,
          _ => Colors.grey,
        };
        return ListTile(
          leading: Icon(Icons.circle, color: statusColor, size: 12),
          title: Text(_fmt(t.createdAt)),
          subtitle: Text(t.status.name),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => TaskDetailScreen(
              initialTask: t,
              service: widget.taskService,
            ),
          )),
        );
      },
    );
  }

  String _fmt(DateTime d) {
    final l = d.toLocal();
    return '${l.year}-${l.month.toString().padLeft(2, '0')}-'
        '${l.day.toString().padLeft(2, '0')} '
        '${l.hour.toString().padLeft(2, '0')}:'
        '${l.minute.toString().padLeft(2, '0')}';
  }
}