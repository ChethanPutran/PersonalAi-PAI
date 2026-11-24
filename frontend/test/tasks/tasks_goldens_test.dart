import 'dart:ui' show Size;

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/tasks/task.dart';
import 'package:pai_mobile/tasks/task_events.dart';
import 'package:pai_mobile/ui/tasks/tasks_screen.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import '../ui/golden_harness.dart';
import 'helpers.dart';

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  testWidgets('live states: offline notice, abandoned cancel, capability-driven buttons', (t) async {
    final feed = FakeLiveFeed()..setState(TaskStreamConnection.offline);
    addTearDown(feed.close);
    final svc = CountingTaskService([
      mk('a', title: 'Play music', ageMin: 1, canPause: true, canCancel: true),
      mk('b', title: 'Capture photo', ageMin: 3, canPause: false, canCancel: true,
          blockedReason: 'Atomic call'),
      mk('c', title: 'Record voice memo', status: TaskStatus.paused, ageMin: 8,
          canPause: false, canResume: true, canCancel: true),
      mk('d', title: 'Open image viewer', status: TaskStatus.cancelled, ageMin: 20, canCancel: false,
          cancelOutcome: TaskCancelOutcome.abandoned),
    ]);
    await pumpHud(t, TasksScreen(service: svc, live: feed), size: const Size(360, 920));
    for (var i = 0; i < 6; i++) {
      await t.pump(const Duration(milliseconds: 60));
    }
    await expectGolden(t, 'tasks_live_states');
  });

  testWidgets('retrying card: amber RETRYING 2/3 chip + countdown line', (t) async {
    final feed = FakeLiveFeed();
    addTearDown(feed.close);
    final svc = CountingTaskService([
      mk('a', title: 'Play music', status: TaskStatus.retrying, ageMin: 1, canCancel: true,
          retry: TaskRetry(
            attempt: 2,
            maxAttempts: 3,
            nextRetryAt: t0.add(const Duration(seconds: 12)),
            delaySeconds: 12,
            lastError: 'Device did not respond in 60s',
          )),
      mk('b', title: 'Capture photo', ageMin: 3, canPause: true, canCancel: true),
    ]);
    await pumpHud(t, TasksScreen(service: svc, live: feed), size: const Size(360, 420));
    for (var i = 0; i < 6; i++) {
      await t.pump(const Duration(milliseconds: 60));
    }
    await expectGolden(t, 'tasks_retrying');
  });
}
