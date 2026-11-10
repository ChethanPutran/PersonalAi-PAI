import 'dart:convert';

import 'package:flutter/material.dart';

import '../../plugins/models/plugin_capability.dart';
import '../../plugins/plugin_manager.dart';
import '../../tasks/local_task.dart';
import '../../tasks/local_task_runner.dart';

class TestTaskScreen extends StatefulWidget {
  final PluginManager pluginManager;
  final LocalTaskRunner runner;

  const TestTaskScreen({
    super.key,
    required this.pluginManager,
    required this.runner,
  });

  @override
  State<TestTaskScreen> createState() => _TestTaskScreenState();
}

class _TestTaskScreenState extends State<TestTaskScreen> {
  PluginCapability? _selected;
  final _paramsController = TextEditingController(text: '{}');
  String? _paramsError;

  @override
  void dispose() {
    _paramsController.dispose();
    super.dispose();
  }

  List<({String pluginName, PluginCapability cap})> _capabilities() {
    final out = <({String pluginName, PluginCapability cap})>[];
    for (final p in widget.pluginManager.plugins) {
      if (!p.isInstalled || !p.isEnabled) continue;
      for (final c in p.capabilityDetails) {
        out.add((pluginName: p.name, cap: c));
      }
    }
    return out;
  }

  void _run() {
    final selected = _selected;
    if (selected == null) return;

    Map<String, dynamic> params;
    try {
      final raw = _paramsController.text.trim();
      params = raw.isEmpty
          ? {}
          : (jsonDecode(raw) as Map).cast<String, dynamic>();
      setState(() => _paramsError = null);
    } catch (e) {
      setState(() => _paramsError = 'Invalid JSON: $e');
      return;
    }

    widget.runner.enqueue(capability: selected.id, parameters: params);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Test Task'),
        actions: [
          IconButton(
            tooltip: 'Clear finished',
            icon: const Icon(Icons.clear_all),
            onPressed: widget.runner.clearFinished,
          ),
        ],
      ),
      body: Column(
        children: [
          _buildComposer(),
          const Divider(height: 1),
          Expanded(child: _buildTaskList()),
        ],
      ),
    );
  }

  Widget _buildComposer() {
    final caps = _capabilities();

    return Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (caps.isEmpty)
            const Padding(
              padding: EdgeInsets.only(bottom: 8),
              child: Text(
                'No enabled capabilities. Install a plugin first.',
                style: TextStyle(color: Colors.orange),
              ),
            )
          else
            DropdownButtonFormField<PluginCapability>(
              value: _selected,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Capability',
                border: OutlineInputBorder(),
                isDense: true,
              ),
              items: caps
                  .map((e) => DropdownMenuItem(
                        value: e.cap,
                        child: Text(
                          '${e.cap.id}  ·  ${e.pluginName}',
                          style: const TextStyle(
                              fontSize: 13, fontFamily: 'monospace'),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ))
                  .toList(),
              onChanged: (v) => setState(() => _selected = v),
            ),
          const SizedBox(height: 8),
          TextField(
            controller: _paramsController,
            decoration: InputDecoration(
              labelText: 'Parameters (JSON)',
              border: const OutlineInputBorder(),
              isDense: true,
              errorText: _paramsError,
            ),
            maxLines: 2,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
          ),
          const SizedBox(height: 8),
          ElevatedButton.icon(
            icon: const Icon(Icons.play_arrow),
            label: const Text('Create & Run Task'),
            onPressed: _selected == null ? null : _run,
          ),
        ],
      ),
    );
  }

  Widget _buildTaskList() {
    return AnimatedBuilder(
      animation: widget.runner,
      builder: (_, __) {
        final tasks = widget.runner.tasks;
        if (tasks.isEmpty) {
          return const Center(
            child: Padding(
              padding: EdgeInsets.all(24),
              child: Text(
                'No test tasks yet.\n'
                'Pick a capability above and tap "Create & Run Task".',
                textAlign: TextAlign.center,
              ),
            ),
          );
        }
        return ListView.builder(
          itemCount: tasks.length,
          itemBuilder: (_, i) => _buildTaskTile(tasks[i]),
        );
      },
    );
  }

  Widget _buildTaskTile(LocalTask task) {
    final pending = task.status == LocalTaskStatus.pending;
    final paused = task.status == LocalTaskStatus.paused;
    final running = task.status == LocalTaskStatus.running;

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: ExpansionTile(
        leading: _statusIcon(task.status),
        title: Text(
          task.capability,
          style: const TextStyle(fontSize: 13, fontFamily: 'monospace'),
        ),
        subtitle: Text(
          '${_statusLabel(task.status)} · ${_elapsed(task)}',
          style: const TextStyle(fontSize: 11),
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (pending)
              IconButton(
                tooltip: 'Pause',
                icon: const Icon(Icons.pause),
                onPressed: () => widget.runner.pause(task.id),
              ),
            if (paused)
              IconButton(
                tooltip: 'Resume',
                icon: const Icon(Icons.play_arrow),
                onPressed: () => widget.runner.resume(task.id),
              ),
            if (!task.isFinished)
              IconButton(
                tooltip: 'Cancel',
                icon: const Icon(Icons.stop),
                onPressed: () => widget.runner.cancel(task.id),
              ),
          ],
        ),
        children: [
          if (task.parameters.isNotEmpty)
            _kv('Parameters', task.parameters),
          if (task.result != null) _kv('Result', task.result!),
          if (task.error != null) _kv('Error', task.error!),
          if (running)
            const Padding(
              padding: EdgeInsets.all(12),
              child: LinearProgressIndicator(),
            ),
        ],
      ),
    );
  }

  Widget _kv(String label, dynamic value) {
    final text = value is String
        ? value
        : const JsonEncoder.withIndent('  ').convert(value);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: const TextStyle(
                  fontWeight: FontWeight.w600, fontSize: 12)),
          const SizedBox(height: 4),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: Colors.grey.shade100,
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: Colors.grey.shade300),
            ),
            child: SelectableText(
              text,
              style:
                  const TextStyle(fontFamily: 'monospace', fontSize: 11),
            ),
          ),
        ],
      ),
    );
  }

  Widget _statusIcon(LocalTaskStatus s) {
    switch (s) {
      case LocalTaskStatus.pending:
        return const Icon(Icons.schedule, color: Colors.orange);
      case LocalTaskStatus.running:
        return const SizedBox(
          width: 20,
          height: 20,
          child: CircularProgressIndicator(strokeWidth: 2),
        );
      case LocalTaskStatus.paused:
        return const Icon(Icons.pause_circle, color: Colors.blue);
      case LocalTaskStatus.completed:
        return const Icon(Icons.check_circle, color: Colors.green);
      case LocalTaskStatus.failed:
        return const Icon(Icons.error, color: Colors.red);
      case LocalTaskStatus.cancelled:
        return const Icon(Icons.cancel, color: Colors.grey);
    }
  }

  String _statusLabel(LocalTaskStatus s) {
    switch (s) {
      case LocalTaskStatus.pending:
        return 'pending';
      case LocalTaskStatus.running:
        return 'running';
      case LocalTaskStatus.paused:
        return 'paused';
      case LocalTaskStatus.completed:
        return 'completed';
      case LocalTaskStatus.failed:
        return 'failed';
      case LocalTaskStatus.cancelled:
        return 'cancelled';
    }
  }

  String _elapsed(LocalTask t) {
    if (t.startedAt == null) return 'not started';
    final end = t.endedAt ?? DateTime.now();
    return '${end.difference(t.startedAt!).inMilliseconds}ms';
  }
}