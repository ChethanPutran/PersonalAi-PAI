import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:pai_mobile/chat/chat_service.dart';
import 'package:pai_mobile/chat/chat_stream.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/device/repositories/device_repository.dart';
import 'package:pai_mobile/models/chat.dart';
import 'package:pai_mobile/models/device.dart';
import 'package:pai_mobile/models/schedule.dart';
import 'package:pai_mobile/models/user.dart';
import 'package:pai_mobile/plugins/models/catalog_response.dart';
import 'package:pai_mobile/plugins/models/plugin_info.dart';
import 'package:pai_mobile/plugins/models/plugin_update.dart';
import 'package:pai_mobile/plugins/plugin_manager.dart';
import 'package:pai_mobile/profile/profile_service.dart';
import 'package:pai_mobile/schedules/schedule_service.dart';
import 'package:pai_mobile/tasks/task.dart';
import 'package:pai_mobile/tasks/task_service.dart';

ApiClient fakeApi() => ApiClient(baseUrl: 'http://backend.test', apiPrefix: '/api/v1');

// ------------------------------------------------------------------ tasks

class FakeTaskService extends TaskService {
  FakeTaskService(this.items, {this.fail}) : super(fakeApi());
  final List<Task> items;
  final Object? fail;

  @override
  Future<List<Task>> getTasks() async {
    if (fail != null) throw fail!;
    return items;
  }

  @override
  Future<Task> getTask(String id) async => items.firstWhere((t) => t.id == id);
  @override
  Future<void> pause(String id) async {}
  @override
  Future<void> resume(String id) async {}
  @override
  Future<void> cancel(String id) async {}
}

/// The pinned "now" used by every golden (see [PaiClock]); local time so no timezone shifts.
final DateTime pinnedNow = DateTime(2026, 11, 23, 12, 0, 0);

DateTime ago(Duration d) => pinnedNow.subtract(d);

List<Task> sampleTasks() => [
      Task(
        id: 't1', title: 'Capture photo', status: TaskStatus.running,
        capability: 'camera.capture', deviceId: 'pixel-8', createdAt: ago(const Duration(seconds: 20)),
        startedAt: ago(const Duration(seconds: 12)),
      ),
      Task(
        id: 't2', title: 'Remind me to call Mom', status: TaskStatus.completed,
        capability: 'schedule.create', deviceId: 'self', createdAt: ago(const Duration(minutes: 5)),
        startedAt: ago(const Duration(minutes: 5)), completedAt: ago(const Duration(minutes: 5)),
      ),
      Task(
        id: 't3', title: 'Open image viewer', status: TaskStatus.failed,
        capability: 'image.open', deviceId: 'desk-linux', createdAt: ago(const Duration(minutes: 42)),
        error: 'Device desk-linux did not respond in 30.0s', retryCount: 2, maxRetries: 3,
      ),
      Task(
        id: 't4', title: 'Record voice memo', status: TaskStatus.paused,
        capability: 'audio.record', deviceId: 'pixel-8', createdAt: ago(const Duration(hours: 2)),
        startedAt: ago(const Duration(hours: 2)),
      ),
      Task(
        id: 't5', title: 'Sync files to laptop', status: TaskStatus.queued,
        capability: 'files.copy', deviceId: 'desk-linux', createdAt: ago(const Duration(hours: 5)),
      ),
    ];

// ------------------------------------------------------------------- chat

class FakeChatService extends ChatService {
  FakeChatService() : super(fakeApi());
  final StreamController<ChatStreamEvent> stream = StreamController<ChatStreamEvent>();

  @override
  String? get sessionId => null;
  @override
  Future<List<ChatSessionSummary>> listSessions() async => [
        ChatSessionSummary(id: 's1', title: 'Photo from desktop', messageCount: 6),
        ChatSessionSummary(id: 's2', title: 'Morning reminders', messageCount: 3),
      ];
  @override
  Stream<ChatStreamEvent> sendMessageStream({
    required String deviceId,
    required String message,
    String mode = 'text',
    String? idempotencyKey,
    dynamic client,
  }) =>
      stream.stream;
}

// -------------------------------------------------------------- schedules

class FakeScheduleService extends ScheduleService {
  FakeScheduleService(this.items) : super(api: fakeApi());
  final List<Schedule> items;
  @override
  Future<List<Schedule>> list() async => items;
}

List<Schedule> sampleSchedules() {
  // Far-future fixed dates: the labels ('Nov 24 09:00') never depend on the real clock.
  final now = DateTime(2030, 1, 1, 9, 0);
  Schedule s(String id, String title, String kind, {bool enabled = true, DateTime? next, String? cron, int? every}) =>
      Schedule(
        id: id, userId: 'u', title: title, kind: kind, timezone: 'UTC',
        capability: 'system.notifications.show', parameters: const {}, catchUp: 'once',
        enabled: enabled, nextFireAt: next, cron: cron, intervalSeconds: every,
        fireAt: kind == 'one_shot' ? next : null, createdAt: now, updatedAt: now,
      );
  return [
    s('a', 'Call Mom', 'one_shot', next: DateTime(2030, 1, 5, 18, 30)),
    s('b', 'Daily standup', 'cron', cron: '0 9 * * 1-5', next: DateTime(2030, 1, 6, 9, 0)),
    s('c', 'Drink water', 'interval', every: 7200, next: DateTime(2030, 1, 4, 10, 15)),
    s('d', 'Weekly backup', 'cron', cron: '0 3 * * 0', enabled: false),
  ];
}

// ---------------------------------------------------------------- profile

DeviceInfo device(String id, String name, String platform, {bool connected = true, bool current = false}) => DeviceInfo(
      id: id, name: name, appVersion: '1.0.0', runtimeVersion: '3.11', platform: platform,
      osVersion: platform == 'android' ? '15' : '6.8', architecture: platform == 'android' ? 'arm64' : 'x86_64',
      deviceType: platform == 'android' ? 'phone' : 'desktop', status: connected ? 'online' : 'offline',
      connected: connected, isCurrent: current, lastSeen: DateTime.now().subtract(Duration(minutes: connected ? 0 : 150)),
    );

class FakeProfileService extends ProfileService {
  FakeProfileService() : super(api: fakeApi());
  @override
  Future<UserProfile> getProfile() async =>
      UserProfile(id: 'u1', email: 'chethan@example.com', isAdmin: true, createdAt: DateTime(2026, 3, 4));
  @override
  Future<List<DeviceInfo>> getDevices({required String userId, String? currentDeviceId}) async => [
        device('pixel-8', 'Pixel 8', 'android', current: true),
        device('desk-linux', 'Desk Linux', 'linux'),
        device('old-tablet', 'Old Tablet', 'android', connected: false),
      ];
}

class FakeDeviceRepository extends DeviceRepository {
  FakeDeviceRepository(this._devices) : super(profileService: FakeProfileService());
  final List<DeviceInfo> _devices;
  @override
  DeviceInfo? getDevice(String deviceId) => _devices.where((d) => d.id == deviceId).firstOrNull;
}

// ---------------------------------------------------------------- plugins

PluginInfo plugin(String id, String name, List<String> caps) => PluginInfo(
      id: id, name: name, version: '1.0.0', type: PluginLanguage.native, runtimeKind: RuntimeKind.device,
      runtimeSpec: const PluginRuntimeSpec(kind: RuntimeKind.device, language: PluginLanguage.native, nativeModule: 'pai.x'),
      capabilities: caps,
    );

class FakePluginManager extends ChangeNotifier implements PluginManager {
  final installed = {'camera'};
  final enabled = {'camera'};
  final here = [
    plugin('camera', 'Camera', ['camera.capture', 'camera.record', 'camera.stream']),
    plugin('notifications', 'Notifications', ['notification.send', 'notification.read']),
    plugin('location', 'Location', ['location.get']),
  ];
  final there = [OtherDeviceGroup(deviceId: 'desk-linux', plugins: [
    plugin('files', 'Files', ['files.list', 'files.read', 'files.write']),
    plugin('screen', 'Screen', ['screen.capture']),
  ])];

  @override
  List<PluginInfo> get thisDevicePlugins => here;
  @override
  List<OtherDeviceGroup> get otherDeviceGroups => there;
  // Camera is installed at 1.0.0 and has 1.1.0 available (shows "v1.0.0 → v1.1.0" + Update).
  final updates = <String, PluginUpdate>{
    'camera': const PluginUpdate(
      pluginId: 'camera',
      installedVersion: '1.0.0',
      latestVersion: '1.1.0',
      updateAvailable: true,
      changelog: [ChangelogEntry(version: '1.1.0', summary: 'Streaming', features: ['Live preview'])],
      addedCapabilities: ['camera.stream'],
    ),
  };

  @override
  PluginUpdate? updateFor(String pluginId, {String? deviceId}) => deviceId == null ? updates[pluginId] : null;
  @override
  bool hasUpdate(String pluginId, {String? deviceId}) => updateFor(pluginId, deviceId: deviceId)?.updateAvailable ?? false;
  @override
  String? installedVersion(String pluginId) => installed.contains(pluginId) ? '1.0.0' : null;
  @override
  List<String>? installedCapabilities(String pluginId) =>
      installed.contains(pluginId) ? here.firstWhere((p) => p.id == pluginId).capabilities : null;
  @override
  bool isInstalled(String pluginId) => installed.contains(pluginId);
  @override
  bool isEnabled(String pluginId) => enabled.contains(pluginId);
  @override
  bool isInstalledOnDevice(String pluginId, String deviceId) => pluginId == 'files';
  @override
  bool isEnabledOnDevice(String pluginId, String deviceId) => pluginId == 'files';
  @override
  RemoteOp remoteStateFor(String deviceId, String pluginId) => const RemoteOp();
  @override
  Future<void> loadCatalog({Duration? maxAge}) async {}

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
