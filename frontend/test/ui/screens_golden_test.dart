// Visual regression ("golden") tests of the HUD redesign. Run
//   flutter test test/ui --update-goldens
// to regenerate the images under test/goldens/, then look at them.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/auth/auth_service.dart';
import 'package:pai_mobile/chat/chat_stream.dart';
import 'package:pai_mobile/profile/settings_service.dart';
import 'package:pai_mobile/services/voice/voice_controller.dart';
import 'package:pai_mobile/services/voice/voice_events.dart';
import 'package:pai_mobile/services/voice/voice_socket.dart';
import 'package:pai_mobile/ui/auth/login_screen.dart';
import 'package:pai_mobile/ui/chat/chat_screen.dart';
import 'package:pai_mobile/ui/plugins/plugins_screen.dart';
import 'package:pai_mobile/ui/profile/profile_screen.dart';
import 'package:pai_mobile/ui/schedules/schedules_screen.dart';
import 'package:pai_mobile/ui/shell/pai_shell.dart';
import 'package:pai_mobile/ui/tasks/task_detail_screen.dart';
import 'package:pai_mobile/ui/tasks/tasks_screen.dart';
import 'package:pai_mobile/ui/voice/voice_screen.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';
import 'package:pai_mobile/tasks/task.dart';

import '../voice/fakes.dart';
import 'fakes.dart';
import 'golden_harness.dart';

Future<void> settle(WidgetTester t) async {
  for (var i = 0; i < 6; i++) {
    await t.pump(const Duration(milliseconds: 60));
  }
}

VoiceController voiceController() => VoiceController(
      socket: VoiceSocket(factory: () async => FakeTransport()),
      mic: FakeMic(),
      player: FakePlayer(),
      pingEvery: const Duration(hours: 1),
    );

const ready = SessionReady(
  voiceSessionId: 'v', chatSessionId: 'c', inputSampleRate: 16000, outputSampleRate: 24000,
  maxUtteranceSeconds: 30, idleTimeoutSeconds: 120, maxSessionSeconds: 3600,
  stt: 'vosk', tts: 'piper', partialTranscripts: true, bargeIn: true,
);

void main() {
  setUpAll(loadPaiFonts);
  // Gradients are dithered and bloat PNGs; most goldens use a flat backdrop (the components
  // are what they check). Tests that want the full backdrop turn it back on explicitly.
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  group('login', () {
    testWidgets('phone (full backdrop)', (t) async {
      HudBackground.debugFlat = false;
      await pumpHud(t, LoginScreen(auth: AuthService(baseUrl: 'http://backend.test'), onAuthenticated: () {}));
      await expectGolden(t, 'login_phone');
    });
  });

  group('voice', () {
    Future<VoiceController> open(WidgetTester t, {Size size = phone}) async {
      final c = voiceController();
      await pumpHud(t, VoiceScreen(controller: c, deviceId: 'dev'), size: size);
      c.handleEvent(ready);
      c.connection = VoiceConnection.connected;
      return c;
    }

    testWidgets('listening with live captions', (t) async {
      final c = await open(t);
      c.handleEvent(const StateChanged(VoiceState.listening));
      c.handleEvent(const TranscriptPartial('t', 'turn on the kitchen lights and'));
      await settle(t);
      await expectGolden(t, 'voice_listening');
      await t.runAsync(() => c.stop().timeout(const Duration(seconds: 2)));
    });

    testWidgets('thinking', (t) async {
      final c = await open(t);
      c.handleEvent(const TranscriptFinal('t', 'What is the weather in Berlin?'));
      c.handleEvent(const StateChanged(VoiceState.thinking));
      await settle(t);
      await expectGolden(t, 'voice_thinking');
      await t.runAsync(() => c.stop().timeout(const Duration(seconds: 2)));
    });

    testWidgets('speaking', (t) async {
      final c = await open(t);
      c.handleEvent(const TranscriptFinal('t', 'What is the weather in Berlin?'));
      c.handleEvent(const AssistantTextDelta('t', 'r', "It's ten degrees and cloudy in Berlin right now, "));
      c.handleEvent(const AssistantTextDelta('t', 'r', 'with a light drizzle expected this afternoon.'));
      c.handleEvent(const StateChanged(VoiceState.speaking));
      await settle(t);
      await expectGolden(t, 'voice_speaking');
      await t.runAsync(() => c.stop().timeout(const Duration(seconds: 2)));
    });

    testWidgets('speaking on desktop', (t) async {
      final c = await open(t, size: desktop);
      c.handleEvent(const AssistantTextDelta('t', 'r', 'Done. I took the photo and saved it to your pictures folder.'));
      c.handleEvent(const StateChanged(VoiceState.speaking));
      await settle(t);
      await expectGolden(t, 'voice_speaking_desktop');
      await t.runAsync(() => c.stop().timeout(const Duration(seconds: 2)));
    });
  });

  group('chat', () {
    testWidgets('empty state', (t) async {
      await pumpHud(t, ChatScreen(service: FakeChatService(), deviceId: 'dev'));
      await settle(t);
      await expectGolden(t, 'chat_empty');
    });

    testWidgets('streaming assistant reply', (t) async {
      final svc = FakeChatService();
      await pumpHud(t, ChatScreen(service: svc, deviceId: 'dev'));
      await settle(t);
      await t.enterText(find.byType(TextField), 'Take a photo with the desktop camera');
      await t.tap(find.byTooltip('Send'));
      await t.pump();
      svc.stream.add(const ChatStreamStatus(stage: 'executing', message: 'Running camera.capture on desk-linux…'));
      await settle(t);
      svc.stream.add(const ChatStreamDelta('Done — I captured a photo on your desktop and '));
      svc.stream.add(const ChatStreamDelta('saved it to the Pictures folder. '));
      svc.stream.add(const ChatStreamDelta('Would you like me to open it'));
      await settle(t);
      await expectGolden(t, 'chat_streaming');
      await svc.stream.close();
      await settle(t);
    });
  });

  group('tasks', () {
    testWidgets('list with mixed statuses', (t) async {
      await pumpHud(t, TasksScreen(service: FakeTaskService(sampleTasks())));
      await settle(t);
      await expectGolden(t, 'tasks_list');
    });


    testWidgets('error state', (t) async {
      await pumpHud(
        t,
        TasksScreen(
          service: FakeTaskService(const [], fail: StateError('API error 500: {detail: Failed to list tasks: 3 validation errors for Task}')),
        ),
      );
      await settle(t);
      await expectGolden(t, 'tasks_error');
    });


    testWidgets('detail (failed, with timeline)', (t) async {
      final failed = sampleTasks()[2];
      final task = Task(
        id: failed.id, title: failed.title, status: TaskStatus.failed, capability: failed.capability,
        deviceId: failed.deviceId, error: failed.error, retryCount: 2, maxRetries: 3,
        createdAt: ago(const Duration(minutes: 42)), queuedAt: ago(const Duration(minutes: 42)),
        startedAt: ago(const Duration(minutes: 41)), completedAt: ago(const Duration(minutes: 40)),
        plugin: 'image', executorId: 'device_executor', parameters: const {'path': '/home/me/Pictures/photo.jpg'},
      );
      await pumpHud(t, TaskDetailScreen(initialTask: task, service: FakeTaskService([task])));
      await settle(t);
      await expectGolden(t, 'task_detail_failed');
    });

  });

  group('plugins & devices', () {
    testWidgets('plugins (this device + other devices)', (t) async {
      final devices = [device('desk-linux', 'Desk Linux', 'linux')];
      await pumpHud(
        t,
        PluginsScreen(
          manager: FakePluginManager(),
          deviceRepository: FakeDeviceRepository(devices),
          thisDevice: device('pixel-8', 'Pixel 8', 'android', current: true),
        ),
      );
      await settle(t);
      await expectGolden(t, 'plugins');
    });

    testWidgets('profile with devices', (t) async {
      await pumpHud(
        t,
        ProfileScreen(
          service: FakeProfileService(),
          settings: SettingsService(initial: const {}),
          currentDeviceId: 'pixel-8',
          onLogout: () async {},
        ),
      );
      await settle(t);
      await expectGolden(t, 'profile_devices');
    });
  });

  group('schedules', () {
    testWidgets('reminders', (t) async {
      await pumpHud(
        t,
        SchedulesScreen(
          service: FakeScheduleService(sampleSchedules()),
          taskService: FakeTaskService(const []),
          events: const Stream.empty(),
          profileService: FakeProfileService(),
          currentDeviceId: 'pixel-8',
        ),
      );
      await settle(t);
      await expectGolden(t, 'schedules');
    });
  });

  group('shell', () {
    PaiShell shell(Size _) => PaiShell(
          index: 1,
          onSelect: (_) {},
          onSignOut: () {},
          animateBackground: false,
          destinations: const [
            PaiDestination(icon: Icons.forum_outlined, selectedIcon: Icons.forum, label: 'Chat'),
            PaiDestination(icon: Icons.checklist_rtl_outlined, selectedIcon: Icons.checklist_rtl, label: 'Tasks'),
            PaiDestination(icon: Icons.extension_outlined, selectedIcon: Icons.extension, label: 'Plugins'),
            PaiDestination(icon: Icons.alarm_outlined, selectedIcon: Icons.alarm, label: 'Reminders'),
            PaiDestination(icon: Icons.person_outline, selectedIcon: Icons.person, label: 'Profile'),
          ],
          body: TasksScreen(service: FakeTaskService(sampleTasks())),
        );

    testWidgets('phone: bottom navigation', (t) async {
      await pumpHud(t, shell(phone));
      await settle(t);
      await expectGolden(t, 'shell_phone');
    });

    testWidgets('desktop: navigation rail (full backdrop)', (t) async {
      HudBackground.debugFlat = false;
      await pumpHud(t, shell(desktop), size: desktop);
      await settle(t);
      await expectGolden(t, 'shell_desktop');
    });
  });
}
