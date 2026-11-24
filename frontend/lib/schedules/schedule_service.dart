import 'package:flutter/foundation.dart';
import 'package:pai_mobile/tasks/task.dart';

import '../core/api/api_client.dart';
import '../models/schedule.dart';
import '../models/schedule_capability.dart';
import 'schedule_errors.dart';

export 'schedule_errors.dart';

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


  /// Every action a user may schedule (always includes the plain reminder).
  /// Malformed entries are logged and skipped, never fatal.
  Future<List<SchedulableCapability>> getSchedulableCapabilities() async {
    final data = await _guard('GET', '$_base/capabilities', () => api.get('$_base/capabilities'));
    return SchedulableCapability.listFromJson(
      data,
      onSkip: (item, e) =>
          debugPrint('[ScheduleService] skipping malformed capability $item: $e'),
    );
  }

  Future<Schedule> create({
    required String title,
    String? body,
    required String kind,             // one_shot | cron | interval
    DateTime? fireAt,                 // UTC for one_shot
    String? cron,
    int? intervalSeconds,
    String timezone = 'UTC',
    String capability = kReminderCapability,
    String catchUp = 'once',
    String? targetDeviceId,
    Map<String, dynamic>? parameters, // plugin-action parameters; default = the reminder text
  }) async {
    final payload = <String, dynamic>{
      'title': title,
      'body': body,
      'kind': kind,
      'timezone': timezone,
      'capability': capability,
      'parameters': parameters ?? {'title': title, 'body': body ?? ''},
      'catch_up': catchUp,
      'target_device_id': targetDeviceId,
    };
    if (fireAt != null) payload['fire_at'] = fireAt.toUtc().toIso8601String();
    if (cron != null) payload['cron'] = cron;
    if (intervalSeconds != null) payload['interval_seconds'] = intervalSeconds;

    final data = await _guard('POST', _base, () => api.post(_base, payload));
    return _schedule(data);
  }

  /// PATCH: only the fields given are sent. Changing the capability, parameters or device
  /// makes the server revoke any standing approval — use the returned [Schedule].
  Future<Schedule> update(
    String id, {
    String? title,
    String? body,
    String? kind,
    DateTime? fireAt,
    String? cron,
    int? intervalSeconds,
    String? timezone,
    String? capability,
    Map<String, dynamic>? parameters,
    String? catchUp,
    String? targetDeviceId,
    bool? enabled,
  }) async {
    final payload = <String, dynamic>{
      'title': ?title,
      'body': ?body,
      'kind': ?kind,
      if (fireAt != null) 'fire_at': fireAt.toUtc().toIso8601String(),
      'cron': ?cron,
      'interval_seconds': ?intervalSeconds,
      'timezone': ?timezone,
      'capability': ?capability,
      'parameters': ?parameters,
      'catch_up': ?catchUp,
      'target_device_id': ?targetDeviceId,
      'enabled': ?enabled,
    };
    final data = await _guard('PATCH', '$_base/$id', () => api.patch('$_base/$id', payload));
    return _schedule(data);
  }

  /// Standing approval for the schedule to run on its own for [days] (1–90).
  /// Only ever called from an explicit user confirmation.
  Future<Schedule> approve(String id, {int days = 30}) async {
    final data = await _guard(
        'POST', '$_base/$id/consent', () => api.post('$_base/$id/consent', {'days': days}));
    return _schedule(data);
  }

  Future<Schedule> revoke(String id) async {
    final data = await _guard('DELETE', '$_base/$id/consent', () => api.delete('$_base/$id/consent'));
    return _schedule(data);
  }

  Schedule _schedule(dynamic data) =>
      Schedule.fromJson((data as Map).cast<String, dynamic>());

  /// Runs [call]; logs any failure (method, path, status, body, stack) with debugPrint and
  /// turns a 422 into a typed [ScheduleValidationException].
  Future<dynamic> _guard(String method, String path, Future<dynamic> Function() call) async {
    try {
      return await call();
    } on ApiException catch (e, st) {
      debugPrint('[ScheduleService] $method $path failed: status=${e.statusCode} body=${e.body}');
      debugPrintStack(stackTrace: st, label: '[ScheduleService] $method $path', maxFrames: 6);
      if (e.statusCode == 422) throw ScheduleValidationException.fromBody(e.body);
      rethrow;
    } catch (e, st) {
      debugPrint('[ScheduleService] $method $path failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[ScheduleService] $method $path', maxFrames: 6);
      rethrow;
    }
  }

  Future<Schedule> setEnabled(String id, bool enabled) async {
    final data = await _guard(
        'PATCH', '$_base/$id', () => api.patch('$_base/$id', {'enabled': enabled}));
    return _schedule(data);
  }

  Future<void> delete(String id) async {
    await _guard('DELETE', '$_base/$id', () => api.delete('$_base/$id'));
  }
}