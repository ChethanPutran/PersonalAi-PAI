import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/tasks/task.dart';
import 'package:pai_mobile/ui/tasks/retry_countdown.dart';
import 'package:pai_mobile/ui/tasks/task_detail_screen.dart';
import 'package:pai_mobile/ui/tasks/tasks_screen.dart';
import 'package:pai_mobile/ui/theme/pai_clock.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import '../ui/golden_harness.dart';
import 'helpers.dart';

TaskRetry retryIn(int seconds, {int attempt = 2, int max = 3, String? err = 'Device did not respond in 60s'}) =>
    TaskRetry(
      attempt: attempt,
      maxAttempts: max,
      nextRetryAt: testNow.add(Duration(seconds: seconds)),
      delaySeconds: 12,
      lastError: err,
    );

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  group('parsing', () {
    test('full block', () {
      final r = TaskRetry.fromJson({
        'attempt': 2,
        'max_attempts': 3,
        'next_retry_at': '2026-11-23T12:00:12Z',
        'delay_seconds': 12.5,
        'last_error': 'boom',
      })!;
      expect(r.attempt, 2);
      expect(r.maxAttempts, 3);
      expect(r.nextRetryAt!.toUtc(), DateTime.utc(2026, 11, 23, 12, 0, 12));
      expect(r.delaySeconds, 12.5);
      expect(r.lastError, 'boom');
    });

    test('null / non-map → null', () {
      expect(TaskRetry.fromJson(null), isNull);
      expect(TaskRetry.fromJson('x'), isNull);
      expect(TaskRetry.fromJson(5), isNull);
    });

    test('missing and odd values degrade, never throw', () {
      final r = TaskRetry.fromJson({'attempt': '2', 'max_attempts': -4, 'next_retry_at': 'not a date', 'delay_seconds': 'x', 'last_error': null})!;
      expect(r.attempt, 2);
      expect(r.maxAttempts, 0);
      expect(r.nextRetryAt, isNull);
      expect(r.lastError, isNull);
      expect(TaskRetry.fromJson(<String, dynamic>{})!.attempt, 0);
    });

    test('Task round-trips retry; absent stays null', () {
      final t = mk('a', status: TaskStatus.retrying, retry: retryIn(12));
      final back = Task.fromJson(t.toJson());
      expect(back.retry!.attempt, 2);
      expect(back.retry!.nextRetryAt, t.retry!.nextRetryAt!.toUtc());
      expect(Task.fromJson(mk('b').toJson()).retry, isNull);
    });

    test('chip label: from retry block, falls back to retry_count, then plain', () {
      expect(mk('a', status: TaskStatus.retrying, retry: retryIn(5)).retryLabel, 'Retrying 2/3');
      expect(mk('a', status: TaskStatus.retrying).retryLabel, 'Retrying');
    });

    test('formatRemaining', () {
      expect(RetryCountdown.formatRemaining(const Duration(seconds: 12)), '12 s');
      expect(RetryCountdown.formatRemaining(const Duration(seconds: 65)), '1 m 05 s');
      expect(RetryCountdown.formatRemaining(const Duration(seconds: -3)), '0 s');
    });
  });

  group('UI', () {
    late FakeLiveFeed feed;
    setUp(() => feed = FakeLiveFeed());
    tearDown(() => feed.close());
    var elapsed = Duration.zero;

    Future<CountingTaskService> pump(WidgetTester t, List<Task> items, {double scale = 1.0}) async {
      elapsed = Duration.zero;
      final svc = CountingTaskService(items);
      await pumpHud(t, TasksScreen(service: svc, live: feed), textScale: scale);
      PaiClock.now = () => testNow.add(elapsed); // after pumpHud pinned it
      await t.pump(const Duration(milliseconds: 100));
      return svc;
    }

    Future<void> advance(WidgetTester t, int seconds) async {
      for (var i = 0; i < seconds; i++) {
        elapsed += const Duration(seconds: 1);
        await t.pump(const Duration(seconds: 1));
      }
    }

    String line(WidgetTester t) => t.widget<Text>(find.byKey(const Key('retry-countdown-text'))).data!;

    testWidgets('chip RETRYING 2/3 and countdown line, counts down, reaches zero', (t) async {
      await pump(t, [mk('a', status: TaskStatus.retrying, retry: retryIn(3), canCancel: true)]);
      expect(find.text('RETRYING 2/3'), findsOneWidget);
      expect(line(t), 'Next attempt in 3 s · Device did not respond in 60s');
      await advance(t, 1);
      expect(line(t), startsWith('Next attempt in 2 s'));
      await advance(t, 2);
      expect(line(t), startsWith('Retrying now…'));
      await advance(t, 5); // ticker stopped; stays calm, no negative numbers
      expect(line(t), startsWith('Retrying now…'));
      expect(find.byTooltip('Cancel'), findsOneWidget, reason: 'cancel stays available during backoff');
    });

    testWidgets('ticker never touches the network', (t) async {
      final svc = await pump(t, [mk('a', status: TaskStatus.retrying, retry: retryIn(30))]);
      final before = (svc.listCalls, svc.getCalls);
      await advance(t, 30);
      expect((svc.listCalls, svc.getCalls), before);
    });

    testWidgets('line disappears (and ticker stops) when the next event leaves RETRYING', (t) async {
      await pump(t, [mk('a', status: TaskStatus.retrying, retry: retryIn(30))]);
      expect(find.byKey(const Key('retry-countdown')), findsOneWidget);
      feed.emit(updated(mk('a', status: TaskStatus.running), seq: 5));
      await t.pump(const Duration(milliseconds: 50));
      expect(find.byKey(const Key('retry-countdown')), findsNothing);
      expect(find.text('RETRYING 2/3'), findsNothing);
      await advance(t, 3); // would throw "timer pending" at teardown if the ticker leaked
    });

    testWidgets('a new event with a new attempt updates chip and countdown', (t) async {
      await pump(t, [mk('a', status: TaskStatus.retrying, retry: retryIn(30))]);
      feed.emit(updated(mk('a', status: TaskStatus.retrying, retry: retryIn(60, attempt: 3)), seq: 6));
      await t.pump(const Duration(milliseconds: 50));
      expect(find.text('RETRYING 3/3'), findsOneWidget);
      expect(line(t), startsWith('Next attempt in 1 m 00 s'));
    });

    testWidgets('missing next_retry_at / last_error are handled', (t) async {
      await pump(t, [
        mk('a', status: TaskStatus.retrying, retry: const TaskRetry(attempt: 1, maxAttempts: 3)),
      ]);
      expect(line(t), 'Retrying soon');
    });

    testWidgets('last_error is expandable and readable', (t) async {
      await pump(t, [mk('a', status: TaskStatus.retrying, retry: retryIn(30, err: 'x' * 200))]);
      expect(find.byKey(const Key('retry-last-error')), findsNothing);
      await t.tap(find.byKey(const Key('retry-countdown')));
      await t.pump();
      expect(find.byKey(const Key('retry-last-error')), findsOneWidget);
      expect(line(t), 'Next attempt in 30 s');
    });

    testWidgets('1.3x text scale: no overflow, semantics label present', (t) async {
      final h = t.ensureSemantics();
      await pump(t, [mk('a', status: TaskStatus.retrying, retry: retryIn(12), canCancel: true)], scale: 1.3);
      expect(t.takeException(), isNull);
      expect(find.bySemanticsLabel(RegExp('Next attempt in 12 s. Last error: Device did not respond')), findsOneWidget);
      h.dispose();
    });

    testWidgets('detail screen shows the same chip text and countdown', (t) async {
      elapsed = Duration.zero;
      final svc = CountingTaskService([mk('a', status: TaskStatus.retrying, retry: retryIn(12), canCancel: true)]);
      await pumpHud(t, TaskDetailScreen(initialTask: svc.items.first, service: svc, live: feed));
      PaiClock.now = () => testNow.add(elapsed);
      await t.pump(const Duration(milliseconds: 100));
      expect(find.text('RETRYING 2/3'), findsOneWidget);
      expect(line(t), startsWith('Next attempt in 12 s'));
      await advance(t, 2);
      expect(line(t), startsWith('Next attempt in 10 s'));
    });
  });
}
