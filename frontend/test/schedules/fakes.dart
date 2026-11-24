import 'package:pai_mobile/models/schedule.dart';
import 'package:pai_mobile/models/schedule_capability.dart';
import 'package:pai_mobile/schedules/schedule_service.dart';

import '../ui/fakes.dart';

/// Far-future fixed "now" used by schedule tests (see PaiClock / pumpHud's `testNow`).
final DateTime kNow = DateTime(2026, 11, 23, 12, 0, 0);

Schedule makeSchedule({
  String id = 's1',
  String title = 'Morning photo',
  String capability = 'camera.capture',
  String kind = 'cron',
  String? cron = '0 9 * * *',
  DateTime? fireAt,
  int? interval,
  String? device = 'pixel-8',
  bool enabled = true,
  Map<String, dynamic> parameters = const {'name': 'sunrise'},
  bool requiresConsent = true,
  ScheduleConsent consent = ScheduleConsent.none,
  DateTime? lastRunAt,
  LastRunStatus? lastRunStatus,
  String? lastRunError,
  ScheduleRisk risk = ScheduleRisk.medium,
  DateTime? nextFireAt,
}) =>
    Schedule(
      id: id, userId: 'u1', title: title, kind: kind, cron: cron, fireAt: fireAt, intervalSeconds: interval,
      targetDeviceId: device, timezone: 'UTC', capability: capability, parameters: parameters,
      catchUp: 'once', enabled: enabled, createdAt: kNow, updatedAt: kNow,
      nextFireAt: nextFireAt ?? DateTime(2026, 11, 24, 9, 0),
      requiresConsent: requiresConsent, consent: consent,
      lastRunAt: lastRunAt, lastRunStatus: lastRunStatus, lastRunError: lastRunError, risk: risk,
      executor: 'device_executor',
    );

ScheduleConsent approvedUntil(DateTime until) =>
    ScheduleConsent(active: true, grantedAt: kNow.subtract(const Duration(days: 1)), expiresAt: until);

List<SchedulableCapability> sampleCaps() => const [
      SchedulableCapability(id: 'system.notifications.show', description: 'Show a notification'),
      SchedulableCapability(
        id: 'camera.capture', description: 'Take a photo', pluginId: 'camera',
        deviceIds: ['pixel-8'], requiresConsent: true, risk: ScheduleRisk.medium,
        parametersSchema: {
          'type': 'object',
          'properties': {
            'name': {'type': 'string', 'description': 'File name', 'minLength': 2, 'maxLength': 30},
            'flash': {'type': 'boolean', 'default': false},
          },
          'required': ['name'],
        },
      ),
      SchedulableCapability(
        id: 'files.read', description: 'Read a file', pluginId: 'files',
        deviceIds: ['pixel-8', 'desk-linux'], requiresConsent: true, risk: ScheduleRisk.high,
        parametersSchema: {
          'type': 'object',
          'properties': {'path': {'type': 'string'}},
          'required': ['path'],
        },
      ),
      SchedulableCapability(
        id: 'audio.play', description: 'Play a sound', pluginId: 'desktop', deviceIds: ['desk-linux'],
        parametersSchema: {
          'type': 'object',
          'properties': {
            'volume': {'type': 'integer', 'minimum': 0, 'maximum': 100, 'default': 50},
            'mode': {'type': 'string', 'enum': ['chime', 'alarm']},
          },
        },
      ),
      SchedulableCapability(
        id: 'shell.exec', description: 'Run a shell command', pluginId: 'shell', deviceIds: ['desk-linux'],
        requiresConsent: true, risk: ScheduleRisk.critical, schedulable: false,
        blockedReason: 'Shell commands cannot run unattended.',
      ),
    ];

/// ScheduleService without a network: records calls and returns canned results.
class FakeSchedulesApi extends ScheduleService {
  FakeSchedulesApi({List<Schedule>? items, List<SchedulableCapability>? caps})
      : items = items ?? [],
        caps = caps ?? sampleCaps(),
        super(api: fakeApi());

  List<Schedule> items;
  List<SchedulableCapability> caps;
  Object? capsError;
  Object? createError;
  Object? updateError;
  Object? approveError;

  final List<Map<String, Object?>> created = [];
  final List<Map<String, Object?>> updated = [];
  final List<(String, int)> approved = [];
  final List<String> revoked = [];

  /// What the "server" returns for create/update (default: a consent-requiring schedule).
  Schedule Function(Map<String, Object?> payload)? onCreate;
  Schedule Function(String id, Map<String, Object?> payload)? onUpdate;

  @override
  Future<List<Schedule>> list() async => items;

  @override
  Future<List<SchedulableCapability>> getSchedulableCapabilities() async {
    if (capsError != null) throw capsError!;
    return caps;
  }

  @override
  Future<Schedule> create({
    required String title,
    String? body,
    required String kind,
    DateTime? fireAt,
    String? cron,
    int? intervalSeconds,
    String timezone = 'UTC',
    String capability = kReminderCapability,
    String catchUp = 'once',
    String? targetDeviceId,
    Map<String, dynamic>? parameters,
  }) async {
    final payload = <String, Object?>{
      'title': title, 'body': body, 'kind': kind, 'fireAt': fireAt, 'cron': cron,
      'intervalSeconds': intervalSeconds, 'timezone': timezone, 'capability': capability,
      'targetDeviceId': targetDeviceId, 'parameters': parameters,
    };
    created.add(payload);
    if (createError != null) throw createError!;
    return onCreate?.call(payload) ??
        makeSchedule(
          id: 'new', title: title, capability: capability, kind: kind, cron: cron, device: targetDeviceId,
          parameters: parameters ?? {'title': title}, requiresConsent: capability != kReminderCapability,
        );
  }

  @override
  Future<Schedule> update(
    String id, {
    String? title, String? body, String? kind, DateTime? fireAt, String? cron, int? intervalSeconds,
    String? timezone, String? capability, Map<String, dynamic>? parameters, String? catchUp,
    String? targetDeviceId, bool? enabled,
  }) async {
    final payload = <String, Object?>{
      'title': title, 'body': body, 'kind': kind, 'cron': cron, 'capability': capability,
      'parameters': parameters, 'targetDeviceId': targetDeviceId,
    };
    updated.add(payload);
    if (updateError != null) throw updateError!;
    final current = items.firstWhere((s) => s.id == id, orElse: () => makeSchedule(id: id));
    return onUpdate?.call(id, payload) ?? current;
  }

  @override
  Future<Schedule> approve(String id, {int days = 30}) async {
    approved.add((id, days));
    if (approveError != null) throw approveError!;
    final s = items.firstWhere((x) => x.id == id, orElse: () => makeSchedule(id: id));
    return makeSchedule(
      id: id, title: s.title, capability: s.capability, device: s.targetDeviceId,
      parameters: s.parameters, consent: approvedUntil(kNow.add(Duration(days: days))),
    );
  }

  @override
  Future<Schedule> revoke(String id) async {
    revoked.add(id);
    final s = items.firstWhere((x) => x.id == id, orElse: () => makeSchedule(id: id));
    return makeSchedule(id: id, title: s.title, capability: s.capability, device: s.targetDeviceId,
        parameters: s.parameters);
  }
}
