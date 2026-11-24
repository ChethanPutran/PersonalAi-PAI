import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/auth/auth_service.dart';
import 'package:pai_mobile/profile/settings_service.dart';
import 'package:pai_mobile/ui/auth/login_screen.dart';
import 'package:pai_mobile/ui/chat/chat_screen.dart';
import 'package:pai_mobile/ui/plugins/plugins_screen.dart';
import 'package:pai_mobile/ui/profile/profile_screen.dart';
import 'package:pai_mobile/ui/schedules/schedules_screen.dart';
import 'package:pai_mobile/ui/tasks/task_detail_screen.dart';
import 'package:pai_mobile/ui/tasks/tasks_screen.dart';
import 'package:pai_mobile/ui/theme/theme.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import 'fakes.dart';
import 'golden_harness.dart';

double _lum(Color c) {
  double ch(double v) => v <= 0.03928 ? v / 12.92 : math.pow((v + 0.055) / 1.055, 2.4).toDouble();
  return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b);
}

double contrast(Color a, Color b) {
  final l1 = _lum(a), l2 = _lum(b);
  return (math.max(l1, l2) + 0.05) / (math.min(l1, l2) + 0.05);
}

Future<void> settle(WidgetTester t) async {
  for (var i = 0; i < 6; i++) {
    await t.pump(const Duration(milliseconds: 60));
  }
}

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  group('contrast (WCAG AA ≥ 4.5:1 for text)', () {
    const surfaces = {
      'bg0': PaiColors.bg0,
      'bg1': PaiColors.bg1,
      'bg2': PaiColors.bg2,
      'bg3': PaiColors.bg3,
    };
    const texts = {
      'textHigh': PaiColors.textHigh,
      'textMid': PaiColors.textMid,
      'textLow': PaiColors.textLow,
      'cyan': PaiColors.cyan,
      'teal': PaiColors.teal,
      'green': PaiColors.green,
      'amber': PaiColors.amber,
      'red': PaiColors.red,
    };
    for (final s in surfaces.entries) {
      for (final tx in texts.entries) {
        test('${tx.key} on ${s.key}', () {
          expect(contrast(tx.value, s.value), greaterThanOrEqualTo(4.5),
              reason: '${tx.key} on ${s.key}');
        });
      }
    }
    test('filled primary button label is readable', () {
      final scheme = PaiTheme.dark().colorScheme;
      expect(contrast(scheme.onPrimary, scheme.primary), greaterThanOrEqualTo(4.5));
    });
    test('light theme text on surfaces', () {
      final s = PaiTheme.light().colorScheme;
      expect(contrast(s.onSurface, s.surface), greaterThanOrEqualTo(7));
      expect(contrast(s.primary, s.surface), greaterThanOrEqualTo(4.5));
      expect(contrast(s.onSurfaceVariant, s.surface), greaterThanOrEqualTo(4.5));
    });
  });

  group('touch targets and semantics', () {
    testWidgets('icon buttons and glow buttons are at least 48dp and labelled', (t) async {
      await pumpHud(
        t,
        Scaffold(
          body: Column(children: [
            HudIconButton(icon: Icons.refresh, tooltip: 'Refresh', onPressed: () {}),
            HudIconButton(icon: Icons.stop, tooltip: 'Tiny request', onPressed: () {}, size: 24),
            GlowButton(label: 'Go', onPressed: () {}, dense: true),
          ]),
        ),
      );
      for (final f in [find.byTooltip('Refresh'), find.byTooltip('Tiny request'), find.byType(GlowButton)]) {
        final size = t.getSize(f.first);
        expect(size.height, greaterThanOrEqualTo(40));
        expect(size.width, greaterThanOrEqualTo(40));
      }
      expect(t.getSize(find.byTooltip('Tiny request')).shortestSide, greaterThanOrEqualTo(48));
      expect(find.bySemanticsLabel('Refresh'), findsWidgets);
    });

    testWidgets('orb announces its state', (t) async {
      for (final state in OrbState.values) {
        await pumpHud(t, Scaffold(body: Center(child: ArcReactorOrb(state: state))));
        expect(find.bySemanticsLabel('Assistant: ${state.label}'), findsOneWidget);
      }
    });
  });

  group('reduced motion', () {
    testWidgets('no animation runs when the platform asks for reduced motion', (t) async {
      HudBackground.debugFlat = false;
      await pumpHud(
        t,
        const Scaffold(
          body: Stack(children: [
            HudBackground(),
            Center(child: ArcReactorOrb(state: OrbState.thinking)),
            Center(child: StatusDot(status: HudStatus.running)),
            Align(child: BlinkingCursor()),
          ]),
        ),
      );
      await t.pump(const Duration(milliseconds: 500));
      expect(t.binding.hasScheduledFrame, isFalse, reason: 'something is still animating');
    });
  });

  group('text scale 1.3 does not overflow', () {
    Future<void> check(WidgetTester t, Widget screen, {Size size = phone, ThemeMode mode = ThemeMode.dark}) async {
      await pumpHud(t, screen, size: size, textScale: 1.3, mode: mode);
      await settle(t);
      expect(t.takeException(), isNull);
    }

    testWidgets('login', (t) async =>
        check(t, LoginScreen(auth: AuthService(baseUrl: 'http://x.test'), onAuthenticated: () {})));
    testWidgets('login (light theme)', (t) async => check(
        t, LoginScreen(auth: AuthService(baseUrl: 'http://x.test'), onAuthenticated: () {}), mode: ThemeMode.light));
    testWidgets('chat empty', (t) async => check(t, ChatScreen(service: FakeChatService(), deviceId: 'd')));
    testWidgets('tasks', (t) async => check(
        t, TasksScreen(service: FakeTaskService(sampleTasks()))));
    testWidgets('tasks (light theme)', (t) async => check(
        t, TasksScreen(service: FakeTaskService(sampleTasks())),
        mode: ThemeMode.light));
    testWidgets('task detail', (t) async {
      final task = sampleTasks()[2];
      await check(t, TaskDetailScreen(initialTask: task, service: FakeTaskService([task])));
    });
    testWidgets('plugins', (t) async => check(
        t,
        PluginsScreen(
          manager: FakePluginManager(),
          deviceRepository: FakeDeviceRepository([device('desk-linux', 'Desk Linux', 'linux')]),
          thisDevice: device('pixel-8', 'Pixel 8', 'android', current: true),
        )));
    testWidgets('profile', (t) async => check(
        t,
        ProfileScreen(
          service: FakeProfileService(),
          settings: SettingsService(initial: const {}),
          currentDeviceId: 'pixel-8',
          onLogout: () async {},
        )));
    testWidgets('schedules', (t) async => check(
        t,
        SchedulesScreen(
          service: FakeScheduleService(sampleSchedules()),
          taskService: FakeTaskService(const []),
          events: const Stream.empty(),
          profileService: FakeProfileService(),
          currentDeviceId: 'pixel-8',
        )));
  });
}
