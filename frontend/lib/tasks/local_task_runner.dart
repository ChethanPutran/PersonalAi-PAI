import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';

import '../plugins/capability_dispatcher.dart';
import 'local_task.dart';

/// Runs capability invocations as local tasks with a
/// pending → running → (completed | failed | cancelled) lifecycle.
///
/// Tasks drain one at a time from a FIFO queue. Pause only affects
/// [LocalTaskStatus.pending] tasks (running invokes complete atomically).
/// Cancel is immediate for pending tasks and discards the result for
/// running ones.
class LocalTaskRunner extends ChangeNotifier {
  final CapabilityDispatcher dispatcher;

  final List<LocalTask> _tasks = [];
  final _rand = Random();

  bool _draining = false;
  bool _disposed = false;

  LocalTaskRunner({required this.dispatcher});

  /// Newest first — the UI shows the most recent task on top.
  List<LocalTask> get tasks => List.unmodifiable(_tasks);

  // ------------------------------------------------------------------
  // Public API
  // ------------------------------------------------------------------

  LocalTask enqueue({
    required String capability,
    Map<String, dynamic> parameters = const {},
  }) {
    final task = LocalTask(
      id: 'local-${DateTime.now().microsecondsSinceEpoch}-${_rand.nextInt(9999)}',
      capability: capability,
      parameters: parameters,
      createdAt: DateTime.now(),
    );
    _tasks.insert(0, task);
    notifyListeners();
    unawaited(_drain());
    return task;
  }

  void pause(String id) {
    final t = _find(id);
    if (t == null || t.status != LocalTaskStatus.pending) return;
    t.status = LocalTaskStatus.paused;
    notifyListeners();
  }

  void resume(String id) {
    final t = _find(id);
    if (t == null || t.status != LocalTaskStatus.paused) return;
    t.status = LocalTaskStatus.pending;
    notifyListeners();
    unawaited(_drain());
  }

  void cancel(String id) {
    final t = _find(id);
    if (t == null || t.isFinished) return;
    t.status = LocalTaskStatus.cancelled;
    t.endedAt = DateTime.now();
    notifyListeners();
  }

  void clearFinished() {
    _tasks.removeWhere((t) => t.isFinished);
    notifyListeners();
  }

  // ------------------------------------------------------------------
  // Drain loop
  // ------------------------------------------------------------------

  Future<void> _drain() async {
    if (_draining || _disposed) return;
    _draining = true;
    try {
      while (!_disposed) {
        // Oldest pending first (newest is at index 0).
        LocalTask? next;
        for (final t in _tasks.reversed) {
          if (t.status == LocalTaskStatus.pending) {
            next = t;
            break;
          }
        }
        if (next == null) break;

        final task = next;
        task.status = LocalTaskStatus.running;
        task.startedAt = DateTime.now();
        notifyListeners();

        try {
          final result = await dispatcher.invoke(
            capability: task.capability,
            parameters: task.parameters,
          );
          if (_disposed) return;

          if (task.status != LocalTaskStatus.cancelled) {
            task.result = result;
            task.status = LocalTaskStatus.completed;
            task.endedAt = DateTime.now();
          }
        } catch (e) {
          if (_disposed) return;

          if (task.status != LocalTaskStatus.cancelled) {
            task.error = e.toString();
            task.status = LocalTaskStatus.failed;
            task.endedAt = DateTime.now();
          }
        }
        notifyListeners();
      }
    } finally {
      _draining = false;
    }
  }

  LocalTask? _find(String id) {
    for (final t in _tasks) {
      if (t.id == id) return t;
    }
    return null;
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}