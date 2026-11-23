import 'package:flutter/material.dart';
import 'package:pai_mobile/profile/profile_service.dart';
import 'package:pai_mobile/tasks/task_service.dart';
import 'package:pai_mobile/ui/schedules/schedule_history_screen.dart';

import '../../models/schedule.dart';
import '../../schedules/schedule_service.dart';
import 'schedule_editor_sheet.dart';

class SchedulesScreen extends StatefulWidget {
  final ScheduleService service;
  final TaskService taskService;
  final ProfileService? profileService;     
  final String? currentDeviceId;            

  /// Optional live feed of `schedule.*` events from the WS.
  final Stream<Map<String, dynamic>>? events;

  const SchedulesScreen({
    super.key,
    required this.service,
    required this.taskService,
    this.events,
    this.profileService,
    this.currentDeviceId,
  });

  @override
  State<SchedulesScreen> createState() => _SchedulesScreenState();
}

class _SchedulesScreenState extends State<SchedulesScreen> {
  List<Schedule> _schedules = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
    widget.events?.listen((msg) {
      final type = msg['type']?.toString() ?? '';
      if (type.startsWith('schedule.')) _load();
    });
  }

  Future<void> _load() async {
    setState(() {
      _loading = _schedules.isEmpty;
      _error = null;
    });
    try {
      final list = await widget.service.list();
      if (!mounted) return;
      setState(() {
        _schedules = list;
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

  Future<void> _openEditor() async {
  final created = await showDialog<bool>(
    context: context,
    barrierDismissible: true,
    builder: (_) => Dialog(
      insetPadding: const EdgeInsets.symmetric(horizontal: 24, vertical: 40),
      clipBehavior: Clip.antiAlias,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 520, maxHeight: 760),
        child: IntrinsicHeight(   // ← shrink-wraps on mobile, capped at 760
    child: ScheduleEditorSheet(
      service: widget.service,
      profileService: widget.profileService,
      currentDeviceId: widget.currentDeviceId,
    ),
  ),
      ),
    ),
  );
  if (created == true) await _load();
}

  Future<void> _toggle(Schedule s) async {
    try {
      await widget.service.setEnabled(s.id, !s.enabled);
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Update failed: $e')));
    }
  }

  Future<void> _delete(Schedule s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete reminder?'),
        content: Text('"${s.title}" will be permanently removed.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(ctx).colorScheme.error,
            ),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.service.delete(s.id);
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Delete failed: $e')));
    }
  }

void _openHistory(Schedule s) {
  Navigator.of(context).push(MaterialPageRoute(
    builder: (_) => ScheduleHistoryScreen(
      schedule: s,
      scheduleService: widget.service,
      taskService: widget.taskService,
    ),
  ));
}
  // ------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Reminders'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _loading ? null : _load,
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _openEditor,
        icon: const Icon(Icons.add_alarm),
        label: const Text('New'),
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return ListView(
        padding: const EdgeInsets.all(24),
        children: [
          const Icon(Icons.error_outline, size: 48, color: Colors.red),
          const SizedBox(height: 16),
          Text('Failed to load reminders\n\n$_error',
              textAlign: TextAlign.center),
          const SizedBox(height: 24),
          Center(
            child: ElevatedButton(onPressed: _load, child: const Text('Retry')),
          ),
        ],
      );
    }
    if (_schedules.isEmpty) {
      return ListView(
        padding: const EdgeInsets.all(32),
        children: [
          const SizedBox(height: 60),
          Icon(Icons.alarm_off,
              size: 64,
              color: Theme.of(context).colorScheme.outline),
          const SizedBox(height: 16),
          const Text(
            'No reminders yet',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          const Text(
            'Tap "New" to add your first reminder.',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.grey),
          ),
        ],
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.only(bottom: 88),
      itemCount: _schedules.length,
      separatorBuilder: (_, __) => const Divider(height: 1),
      itemBuilder: (_, i) {
        final s = _schedules[i];
        return ListTile(
          leading: Icon(
            s.enabled ? Icons.alarm : Icons.alarm_off,
            color: s.enabled
                ? Theme.of(context).colorScheme.primary
                : Theme.of(context).colorScheme.outline,
          ),
          title: Text(
            s.title,
            style: TextStyle(
              decoration:
                  s.enabled ? null : TextDecoration.lineThrough,
            ),
          ),
          subtitle: Text('${s.repeatLabel}\n${s.nextLabel}'),
          isThreeLine: true,
          trailing: PopupMenuButton<String>(
            onSelected: (v) {
              if (v == 'history') _openHistory(s);
              if (v == 'toggle') _toggle(s);
              if (v == 'delete') _delete(s);
            },
            itemBuilder: (_) => [
              const PopupMenuItem(value: 'history', child: Text('History')),
              PopupMenuItem(
                value: 'toggle',
                child: Text(s.enabled ? 'Disable' : 'Enable'),
              ),
              const PopupMenuItem(
                value: 'delete',
                child: Text('Delete'),
              ),
            ],
          ),
          onTap: () => _toggle(s),
        );
      },
    );
  }
}