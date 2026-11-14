import 'package:pai_mobile/tasks/task.dart';

import '../core/api/api_client.dart';
import '../models/schedule.dart';

class ScheduleService {
  static const String _base = '/schedules';

  final ApiClient api;

  ScheduleService({required this.api});

  Future<List<Schedule>> list() async {
    final data = await api.get(_base);
    if (data is List) {
      return data
          .whereType<Map>()
          .map((m) => Schedule.fromJson(m.cast<String, dynamic>()))
          .toList();
    }
    return const [];
  }

  Future<List<Task>> getHistory(String scheduleId) async {
  final data = await api.get(
    '$_base/$scheduleId/history',
    query: {'limit': '50'},
  );
  if (data is Map && data['tasks'] is List) {
    return (data['tasks'] as List)
        .whereType<Map>()
        .map((m) => Task.fromJson(m.cast<String, dynamic>()))
        .toList();
  }
  return const [];
}


  Future<Schedule> create({
    required String title,
    String? body,
    required String kind,             // one_shot | cron | interval
    DateTime? fireAt,                 // UTC for one_shot
    String? cron,
    int? intervalSeconds,
    String timezone = 'UTC',
    String capability = 'system.notifications.show',
    String catchUp = 'once',
    String? targetDeviceId,
  }) async {
    final payload = <String, dynamic>{
      'title': title,
      'body': body,
      'kind': kind,
      'timezone': timezone,
      'capability': capability,
      'parameters': {'title': title, 'body': body ?? ''},
      'catch_up': catchUp,
      'target_device_id': targetDeviceId,
    };
    if (fireAt != null) payload['fire_at'] = fireAt.toUtc().toIso8601String();
    if (cron != null) payload['cron'] = cron;
    if (intervalSeconds != null) payload['interval_seconds'] = intervalSeconds;

    final data = await api.post(_base, payload);
    return Schedule.fromJson((data as Map).cast<String, dynamic>());
  }

  Future<Schedule> setEnabled(String id, bool enabled) async {
    final data = await api.patch('$_base/$id', {'enabled': enabled});
    return Schedule.fromJson((data as Map).cast<String, dynamic>());
  }

  Future<void> delete(String id) async {
    await api.delete('$_base/$id');
  }
}