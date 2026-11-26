import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/tasks/task.dart';
import 'package:pai_mobile/ui/tasks/task_detail_screen.dart';
import 'package:pai_mobile/ui/tasks/tasks_screen.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import '../ui/golden_harness.dart';
import 'helpers.dart';

const _denied = TaskErrorInfo(
  code: 'path_outside_roots',
  kind: 'denied',
  message: "That location isn't one this plugin is allowed to use.",
  hint: 'Pick a folder it can access, such as Documents.',
  raw: 'Authorization denied: Path is outside allowed roots',
);

Future<void> settle(WidgetTester t) async {
  for (var i = 0; i < 6; i++) {
    await t.pump(const Duration(milliseconds: 60));
  }
}

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  group('TaskErrorInfo', () {
    test('parses the backend error_info and survives a round trip', () {
      final t = Task.fromJson({
        'id': 't1', 'title': 'x', 'status': 'failed', 'created_at': '2026-11-25T10:00:00Z',
        'error': _denied.raw, 'error_info': _denied.toJson(),
      });
      expect(t.errorInfo!.code, 'path_outside_roots');
      expect(t.errorInfo!.hint, startsWith('Pick a folder'));
      expect(Task.fromJson(t.toJson()).errorInfo!.message, _denied.message);
    });

    test('an older backend (no error_info) still parses', () {
      final t = Task.fromJson({'id': 't1', 'title': 'x', 'status': 'failed', 'created_at': '2026-11-25T10:00:00Z', 'error': 'boom'});
      expect(t.errorInfo, isNull);
      expect(taskFailureText(t), 'boom');
    });

    test('failure text: explanation first, raw second, never blank', () {
      expect(taskFailureText(mk('a', status: TaskStatus.failed, errorInfo: _denied, error: _denied.raw)), _denied.oneLine);
      expect(taskFailureText(mk('a', status: TaskStatus.failed, error: 'raw only')), 'raw only');
      final blank = taskFailureText(mk('a', status: TaskStatus.failed));
      expect(blank, contains('nothing reported why'));
      expect(taskFailureText(mk('a', status: TaskStatus.failed, error: '   ')), blank);
    });
  });

  group('screens say what went wrong', () {
    testWidgets('the list card shows the plain explanation, not the developer string', (t) async {
      final svc = CountingTaskService([mk('a', status: TaskStatus.failed, errorInfo: _denied, error: _denied.raw)]);
      await pumpHud(t, TasksScreen(service: svc, live: null));
      await settle(t);
      final text = (t.widget(find.byKey(const Key('task-failure-text'))) as Text).data!;
      expect(text, contains("isn't one this plugin is allowed to use"));
      expect(text, contains('Pick a folder'));
      expect(text, isNot(contains('Authorization denied')));
    });

    testWidgets('a failed task with no reason at all still explains itself on the card', (t) async {
      final svc = CountingTaskService([mk('a', status: TaskStatus.failed)]);
      await pumpHud(t, TasksScreen(service: svc, live: null));
      await settle(t);
      expect((t.widget(find.byKey(const Key('task-failure-text'))) as Text).data, contains('nothing reported why'));
    });

    testWidgets('the detail screen shows message, next step and the raw text under technical details', (t) async {
      final task = mk('a', status: TaskStatus.failed, errorInfo: _denied, error: _denied.raw);
      await pumpHud(t, TaskDetailScreen(initialTask: task, service: CountingTaskService([task]), live: FakeLiveFeed()));
      await settle(t);
      expect(find.byKey(const Key('task-error-message'), skipOffstage: false), findsOneWidget);
      expect(find.byKey(const Key('task-error-hint'), skipOffstage: false), findsOneWidget);
      expect(find.text(_denied.raw!, skipOffstage: false), findsOneWidget);                  // raw is still there, just not the headline
      expect(find.text('TECHNICAL DETAILS', skipOffstage: false), findsOneWidget);
    });
  });
}
