import 'package:flutter/foundation.dart';

import '../core/api/api_client.dart';
import 'task.dart';

/// A pause / resume / cancel the server refused (HTTP 409):
/// `{"detail": {"code": "control_not_supported" | "control_not_acknowledged" | "invalid_state",
///              "message": "..."}}`.
class TaskControlException implements Exception {
  final String code;
  final String message;
  const TaskControlException(this.code, this.message);

  /// Reads the contract body; tolerates a plain-string `detail` or a missing one.
  factory TaskControlException.fromBody(dynamic body) {
    var code = 'invalid_state';
    String? message;
    if (body is Map) {
      final detail = body['detail'];
      if (detail is Map) {
        code = detail['code']?.toString() ?? code;
        message = detail['message']?.toString();
      } else if (detail != null) {
        message = detail.toString();
      }
    }
    return TaskControlException(code, message ?? 'The server refused this action.');
  }

  @override
  String toString() => message;
}

class TaskService {
  final ApiClient api;

  TaskService(this.api);

  Future<List<Task>> getTasks() async {
    final result = await api.get('/tasks');

    // Backend may return any of:
    //   {"tasks": [...]}         ← current route shape
    //   {"tasks": [...], "total": N}
    //   [...]                    ← bare list (older shape, future shape)
    //
    // The original code did `result['tasks'] as List` with no fallback,
    // which crashes on a bare list with a misleading
    // "Null check operator used on a null value" trace. Handle all three.
    final dynamic raw = result;
    final List<dynamic> items;
    if (raw is Map && raw['tasks'] is List) {
      items = raw['tasks'] as List;
    } else if (raw is List) {
      items = raw;
    } else if (raw is Map && raw['data'] is List) {
      items = raw['data'] as List;
    } else {
      throw StateError('Unexpected /tasks response shape: ${raw.runtimeType}');
    }

    // One malformed task must not blank the whole screen: log it (with its id and the
    // reason) and keep the rest. If NOTHING parses, that is a real contract problem — throw.
    final tasks = <Task>[];
    var skipped = 0;
    for (var i = 0; i < items.length; i++) {
      try {
        tasks.add(Task.fromJson(Map<String, dynamic>.from(items[i] as Map)));
      } catch (e, st) {
        skipped++;
        final id = items[i] is Map ? (items[i] as Map)['id'] : null;
        debugPrint('[TaskService] skipping unparsable task #$i (id=$id): $e');
        debugPrintStack(stackTrace: st, label: '[TaskService] task #$i', maxFrames: 4);
      }
    }
    if (skipped > 0) {
      debugPrint('[TaskService] parsed ${tasks.length}/${items.length} tasks '
          '($skipped skipped)');
    }
    if (tasks.isEmpty && items.isNotEmpty) {
      throw StateError('None of the ${items.length} tasks returned by /tasks could be parsed');
    }
    return tasks;
  }

  Future<Task> getTask(String id) async {
    final result = await api.get('/tasks/$id');

    // Same defensive normalization. Backend may return:
    //   {"task": {...}}          ← current shape
    //   {...}                    ← flat task object
    //
    // `Task.fromJson` will fail if it's neither, so surfacing a clear
    // StateError beats the opaque parse error.
    final dynamic raw = result;
    final Map<String, dynamic> data;
    if (raw is Map && raw['task'] is Map) {
      data = Map<String, dynamic>.from(raw['task'] as Map);
    } else if (raw is Map) {
      data = Map<String, dynamic>.from(raw);
    } else {
      throw StateError(
        'Unexpected /tasks/$id response shape: ${raw.runtimeType}',
      );
    }
    return Task.fromJson(data);
  }

  Future<void> pause(String id) => _control(id, 'pause');

  Future<void> resume(String id) => _control(id, 'resume');

  Future<void> cancel(String id) => _control(id, 'cancel');

  /// POSTs a control. The UI never assumes it worked: the confirming task event updates it.
  /// A 409 means the server refused (e.g. the plugin cannot be paused) → [TaskControlException].
  Future<void> _control(String id, String action) async {
    try {
      await api.post('/tasks/$id/$action', {});
    } on ApiException catch (e) {
      if (e.statusCode == 409) {
        debugPrint('[TaskService] $action $id refused (409): ${e.body}');
        throw TaskControlException.fromBody(e.body);
      }
      rethrow;
    }
  }
}
