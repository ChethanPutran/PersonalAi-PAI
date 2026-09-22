import '../core/api/api_client.dart';
import 'task.dart';


class TaskService {
  final ApiClient api;

  TaskService(this.api);


  Future<List<Task>> getTasks() async {
    final result = await api.get('/tasks');

    return (result['tasks'] as List)
        .map(
          (item) => Task.fromJson(
            Map<String, dynamic>.from(item),
          ),
        )
        .toList();
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