import '../core/api/api_client.dart';
import 'task.dart';

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

    return items
        .map((item) => Task.fromJson(Map<String, dynamic>.from(item as Map)))
        .toList();
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

  Future<void> pause(String id) async {
    await api.post('/tasks/$id/pause', {});
  }

  Future<void> resume(String id) async {
    await api.post('/tasks/$id/resume', {});
  }

  Future<void> cancel(String id) async {
    await api.post('/tasks/$id/cancel', {});
  }
}
