import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/tasks/task.dart';
import 'package:pai_mobile/tasks/task_event_stream.dart';
import 'package:pai_mobile/tasks/task_events.dart';
import 'package:pai_mobile/tasks/task_service.dart';
import 'package:pai_mobile/ui/tasks/live_feed_notice.dart';
import 'package:pai_mobile/ui/tasks/task_detail_screen.dart';
import 'package:pai_mobile/ui/tasks/tasks_screen.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import '../ui/golden_harness.dart';
import 'helpers.dart';

Future<void> settle(WidgetTester t) async {
  for (var i = 0; i < 6; i++) {
    await t.pump(const Duration(milliseconds: 60));
  }
}

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  late FakeLiveFeed feed;
  setUp(() => feed = FakeLiveFeed());
  tearDown(() => feed.close());

  Future<CountingTaskService> pumpList(
    WidgetTester t,
    List<Task> items, {
    TaskLiveFeed? live,
    bool noFeed = false,
    double textScale = 1.0,
  }) async {
    final svc = CountingTaskService(items);
    await pumpHud(t, TasksScreen(service: svc, live: noFeed ? null : (live ?? feed)), textScale: textScale);
    await settle(t);
    return svc;
  }

  group('push, not polling', () {
    testWidgets('exactly one GET on open and NO timer-driven fetch over ten minutes', (t) async {
      final svc = await pumpList(t, [mk('a')]);
      expect(svc.listCalls, 1);
      for (var i = 0; i < 60; i++) {
        await t.pump(const Duration(seconds: 10)); // ten fake minutes
      }
      expect(svc.listCalls, 1, reason: 'no polling loop of any kind');
    });

    testWidgets('a task created while the screen is open appears immediately, with its status', (t) async {
      final svc = await pumpList(t, []);
      expect(find.text('Play music'), findsNothing);

      feed.emit(updated(mk('new', title: 'Play music', status: TaskStatus.running), seq: 1, event: 'task.created'));
      await t.pump(const Duration(milliseconds: 50));

      expect(find.text('Play music'), findsOneWidget);
      expect(find.text('RUNNING'), findsOneWidget);
      expect(svc.listCalls, 1, reason: 'the event carries the task: no REST call');
    });

    testWidgets('an event changes the status chip in place', (t) async {
      final svc = await pumpList(t, [mk('a', title: 'Reminder', status: TaskStatus.running)]);
      expect(find.text('RUNNING'), findsOneWidget);

      feed.emit(updated(mk('a', title: 'Reminder', status: TaskStatus.completed), seq: 2, event: 'task.completed'));
      await t.pump(const Duration(milliseconds: 50));

      expect(find.text('COMPLETED'), findsOneWidget);
      expect(find.text('RUNNING'), findsNothing);
      expect(svc.listCalls, 1);
    });

    testWidgets('upserts keep newest-first order', (t) async {
      await pumpList(t, [mk('mid', title: 'Mid task', ageMin: 10, status: TaskStatus.completed)]);
      feed.emit(updated(mk('new', title: 'Newest task', ageMin: 1), seq: 1));
      feed.emit(updated(mk('old', title: 'Oldest task', ageMin: 60, status: TaskStatus.failed), seq: 2));
      await t.pump(const Duration(milliseconds: 50));
      double y(String s) => t.getTopLeft(find.text(s)).dy;
      expect(y('Newest task'), lessThan(y('Mid task')));
      expect(y('Mid task'), lessThan(y('Oldest task')));
    });

    testWidgets('events that land while the GET is in flight are not lost to a stale snapshot', (t) async {
      final svc = CountingTaskService([])..gate = Completer<List<Task>>();
      await pumpHud(t, TasksScreen(service: svc, live: feed));
      await t.pump(const Duration(milliseconds: 50)); // GET is pending

      feed.emit(updated(mk('x', title: 'Race task', status: TaskStatus.completed), seq: 9));
      await t.pump(const Duration(milliseconds: 50));
      // the snapshot was taken BEFORE the event: it still says running
      svc.gate!.complete([mk('x', title: 'Race task', status: TaskStatus.running)]);
      await settle(t);

      expect(find.text('COMPLETED'), findsOneWidget);
      expect(find.text('RUNNING'), findsNothing);
    });

    testWidgets('the screen stops listening when it is closed', (t) async {
      await pumpList(t, [mk('a')]);
      await t.pumpWidget(const SizedBox());
      feed.emit(updated(mk('b'), seq: 3)); // must not throw (setState after dispose)
      feed.setState(TaskStreamConnection.offline);
      await t.pump(const Duration(milliseconds: 50));
      expect(t.takeException(), isNull);
    });
  });

  group('resync / reconnect', () {
    testWidgets('a resync reloads exactly once', (t) async {
      final svc = await pumpList(t, [mk('a')]);
      svc.items = [mk('a'), mk('b', title: 'Missed task')];

      feed.emit(const TaskStreamResync(reason: 'gap', latestSeq: 40));
      await settle(t);

      expect(svc.listCalls, 2);
      expect(find.text('Missed task'), findsOneWidget);
    });

    testWidgets('resyncs that arrive during a load coalesce into one extra reload', (t) async {
      final svc = CountingTaskService([mk('a')])..gate = Completer<List<Task>>();
      await pumpHud(t, TasksScreen(service: svc, live: feed));
      await t.pump(const Duration(milliseconds: 50));
      for (var i = 0; i < 3; i++) {
        feed.emit(const TaskStreamResync(reason: 'gap', latestSeq: 5));
      }
      await t.pump(const Duration(milliseconds: 50));
      svc.gate!.complete([mk('a')]);
      await settle(t);
      expect(svc.listCalls, 2, reason: 'initial + ONE coalesced reload, not four');
    });

    testWidgets('ordinary events and "ready" never trigger a reload', (t) async {
      final svc = await pumpList(t, [mk('a')]);
      feed.emit(const TaskStreamReady(12));
      feed.emit(updated(mk('a', status: TaskStatus.paused), seq: 13, event: 'task.paused'));
      await settle(t);
      expect(svc.listCalls, 1);
    });

    testWidgets('the offline notice appears when the feed goes offline and goes away when it recovers', (t) async {
      await pumpList(t, [mk('a')]);
      expect(find.byType(LiveFeedNotice), findsNothing);

      feed.setState(TaskStreamConnection.reconnecting); // first miss is silent
      await t.pump(const Duration(milliseconds: 50));
      expect(find.byType(LiveFeedNotice), findsNothing);

      feed.setState(TaskStreamConnection.offline);
      await t.pump(const Duration(milliseconds: 50));
      expect(find.text('Live updates paused — reconnecting…'), findsOneWidget);

      feed.setState(TaskStreamConnection.connected);
      await t.pump(const Duration(milliseconds: 50));
      expect(find.byType(LiveFeedNotice), findsNothing);
    });

    testWidgets('a screen opened while the feed is already offline shows the notice at once', (t) async {
      feed.setState(TaskStreamConnection.offline);
      await pumpList(t, [mk('a')]);
      expect(find.byType(LiveFeedNotice), findsOneWidget);
    });
  });

  group('capability-driven controls', () {
    testWidgets('buttons follow the server flags, per task', (t) async {
      await pumpList(t, [
        mk('a', title: 'Can pause', canPause: true, canResume: false, canCancel: true, ageMin: 1),
        mk('b', title: 'Atomic call', canPause: false, canResume: false, canCancel: true, ageMin: 2),
        mk('c', title: 'Not cancellable', canPause: false, canResume: false, canCancel: false, ageMin: 3),
        mk('d', title: 'Is paused', status: TaskStatus.paused, canPause: false, canResume: true, canCancel: true, ageMin: 4),
        mk('e', title: 'Queued one', status: TaskStatus.queued, canPause: false, canResume: false, canCancel: true, ageMin: 5),
        mk('f', title: 'Finished', status: TaskStatus.completed, canPause: false, canResume: false, canCancel: false, ageMin: 6),
      ]);
      expect(find.byTooltip('Pause'), findsOneWidget); // only 'Can pause'
      expect(find.byTooltip('Resume'), findsOneWidget); // only 'Is paused'
      expect(find.byTooltip('Cancel'), findsNWidgets(4)); // a, b, d, e
    });

    testWidgets('an older server (no flags) keeps the status-based buttons', (t) async {
      await pumpList(t, [
        mk('a', title: 'Running', ageMin: 1),
        mk('b', title: 'Paused', status: TaskStatus.paused, ageMin: 2),
        mk('c', title: 'Done', status: TaskStatus.completed, ageMin: 3),
      ]);
      expect(find.byTooltip('Pause'), findsOneWidget);
      expect(find.byTooltip('Resume'), findsOneWidget);
      expect(find.byTooltip('Cancel'), findsNWidgets(2));
    });

    testWidgets('a refused control (409) shows the SERVER message and changes nothing', (t) async {
      final svc = await pumpList(t, [mk('a', title: 'Music', canPause: true, canCancel: true)]);
      svc.controlError = const TaskControlException('control_not_supported', 'This plugin cannot be paused.');

      await t.tap(find.byTooltip('Pause'));
      await t.pump();
      await t.pump(const Duration(milliseconds: 300));

      expect(find.text('This plugin cannot be paused.'), findsOneWidget); // snackbar
      expect(find.text('RUNNING'), findsOneWidget); // never optimistic
      expect(svc.calls, ['pause:a']);
      expect(svc.listCalls, 1, reason: 'a refusal needs no reload');
    });

    testWidgets('success with a healthy feed does not reload (the event updates the card)', (t) async {
      final svc = await pumpList(t, [mk('a', canPause: true, canCancel: true)]);
      await t.tap(find.byTooltip('Pause'));
      await settle(t);
      expect(svc.calls, ['pause:a']);
      expect(svc.listCalls, 1);
      expect(find.text('RUNNING'), findsOneWidget, reason: 'not optimistic: waits for the event');

      feed.emit(updated(mk('a', status: TaskStatus.paused, canResume: true, canCancel: true), seq: 4, event: 'task.paused'));
      await t.pump(const Duration(milliseconds: 50));
      expect(find.text('PAUSED'), findsOneWidget);
      expect(find.byTooltip('Resume'), findsOneWidget);
    });

    testWidgets('when no event can arrive (feed down) an action reloads exactly once', (t) async {
      feed.setState(TaskStreamConnection.offline);
      final svc = await pumpList(t, [mk('a', canPause: true, canCancel: true)]);
      await t.tap(find.byTooltip('Cancel'));
      await settle(t);
      expect(svc.listCalls, 2);
    });

    testWidgets('an abandoned cancel is explained; an acknowledged one is not', (t) async {
      await pumpList(t, [
        mk('a', title: 'Abandoned', status: TaskStatus.cancelled, canCancel: false,
            cancelOutcome: TaskCancelOutcome.abandoned, ageMin: 1),
        mk('b', title: 'Acked', status: TaskStatus.cancelled, canCancel: false,
            cancelOutcome: TaskCancelOutcome.acked, ageMin: 2),
      ]);
      expect(find.text('Stopped waiting — the device may have finished it'), findsOneWidget);
    });
  });

  group('accessibility', () {
    testWidgets('control buttons are at least 48dp', (t) async {
      await pumpList(t, [mk('a', canPause: true, canCancel: true)]);
      expect(t.getSize(find.byTooltip('Pause')).shortestSide, greaterThanOrEqualTo(48));
      expect(t.getSize(find.byTooltip('Cancel')).shortestSide, greaterThanOrEqualTo(48));
    });

    testWidgets('text scale 1.3 with the offline notice and the abandoned note does not overflow', (t) async {
      feed.setState(TaskStreamConnection.offline);
      await pumpList(t, [
        mk('a', canPause: true, canCancel: true, ageMin: 1),
        mk('b', title: 'Abandoned', status: TaskStatus.cancelled, canCancel: false,
            cancelOutcome: TaskCancelOutcome.abandoned, ageMin: 2),
      ], textScale: 1.3);
      expect(t.takeException(), isNull);
    });
  });

  group('TaskDetailScreen is live too', () {
    Future<CountingTaskService> pumpDetail(WidgetTester t, Task task, {TaskLiveFeed? live}) async {
      final svc = CountingTaskService([task]);
      await pumpHud(t, TaskDetailScreen(initialTask: task, service: svc, live: live ?? feed));
      await settle(t);
      return svc;
    }

    testWidgets('one fetch on open, then no polling at all', (t) async {
      final svc = await pumpDetail(t, mk('a'));
      expect(svc.getCalls, 1);
      for (var i = 0; i < 60; i++) {
        await t.pump(const Duration(seconds: 10));
      }
      expect(svc.getCalls, 1);
    });

    testWidgets('an event for this task updates it instantly; other tasks are ignored', (t) async {
      final svc = await pumpDetail(t, mk('a', title: 'Mine'));
      expect(find.text('RUNNING'), findsWidgets);

      feed.emit(updated(mk('zzz', status: TaskStatus.failed), seq: 1));
      await t.pump(const Duration(milliseconds: 50));
      expect(find.text('FAILED'), findsNothing);

      feed.emit(updated(mk('a', title: 'Mine', status: TaskStatus.completed), seq: 2, event: 'task.completed'));
      await t.pump(const Duration(milliseconds: 50));
      expect(find.text('COMPLETED'), findsWidgets);
      expect(svc.getCalls, 1);
    });

    testWidgets('resync refetches once', (t) async {
      final svc = await pumpDetail(t, mk('a'));
      feed.emit(const TaskStreamResync(reason: 'gap', latestSeq: 9));
      await settle(t);
      expect(svc.getCalls, 2);
    });

    testWidgets('pause is hidden when the plugin cannot pause, with a calm note saying why', (t) async {
      await pumpDetail(t, mk('a', canPause: false, canCancel: true, blockedReason: 'This call is atomic.'));
      expect(find.byTooltip('Pause'), findsNothing);
      expect(find.byTooltip('Cancel'), findsOneWidget);
      expect(find.text("Pause isn't available: This call is atomic."), findsOneWidget);
    });

    testWidgets('a refused control shows the server message', (t) async {
      final svc = await pumpDetail(t, mk('a', canPause: true, canCancel: true));
      svc.controlError = const TaskControlException('control_not_acknowledged', 'The device did not confirm.');
      await t.tap(find.byTooltip('Pause'));
      await t.pump();
      await t.pump(const Duration(milliseconds: 300));
      expect(find.text('The device did not confirm.'), findsOneWidget);
      expect(find.text('RUNNING'), findsWidgets);
    });

    testWidgets('the abandoned-cancel note and the offline notice show on the detail screen', (t) async {
      feed.setState(TaskStreamConnection.offline);
      await pumpDetail(t, mk('a', status: TaskStatus.cancelled, canCancel: false,
          cancelOutcome: TaskCancelOutcome.abandoned));
      expect(find.text('Stopped waiting — the device may have finished it'), findsOneWidget);
      expect(find.byType(LiveFeedNotice), findsOneWidget);
    });
  });
}
