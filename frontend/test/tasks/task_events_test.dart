import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:pai_mobile/chat/sse_client.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/tasks/task.dart';
import 'package:pai_mobile/tasks/task_events.dart';
import 'package:pai_mobile/tasks/task_service.dart';

import 'helpers.dart';

List<SseEvent> parse(String wire) => SseParser().add(wire);

void main() {
  group('SSE frame -> typed event', () {
    test('a task frame becomes TaskUpdated with the whole task and its seq', () {
      final wire = 'id: 12\nevent: task.started\ndata: '
          '${jsonEncode(eventJson(mk('a', status: TaskStatus.running), seq: 12, event: 'task.started'))}\n\n';
      final p = parseTaskStreamFrame(parse(wire).single);
      final e = p.event as TaskUpdated;
      expect((e.seq, e.event, e.task.id, e.task.status), (12, 'task.started', 'a', TaskStatus.running));
      expect(p.seq, 12);
    });

    test('multi-line data: is joined before JSON parsing', () {
      final json = jsonEncode(eventJson(mk('a'), seq: 3, event: 'task.created'));
      final half = json.length ~/ 2;
      final wire = 'id: 3\nevent: task.created\ndata: ${json.substring(0, half)}\ndata: ${json.substring(half)}\n\n';
      // SSE joins data lines with "\n"; JSON tolerates the whitespace only between tokens, so the
      // split must land there — split at a comma boundary to be a valid multi-line payload.
      final commaAt = json.indexOf(',');
      final good = 'id: 3\nevent: task.created\ndata: ${json.substring(0, commaAt + 1)}\n'
          'data: ${json.substring(commaAt + 1)}\n\n';
      expect(parseTaskStreamFrame(parse(good).single).event, isA<TaskUpdated>());
      // a split inside a string literal is invalid JSON → ignored, never thrown
      expect(() => parseTaskStreamFrame(parse(wire).single), returnsNormally);
    });

    test('comments / keep-alives produce no events', () {
      expect(parse(': keep-alive\n\n: another\n\n'), isEmpty);
    });

    test('ready, resync carry their sequence numbers', () {
      final ready = parseTaskStreamFrame(parse('event: ready\ndata: {"latest_seq": 41}\n\n').single);
      expect((ready.event as TaskStreamReady).latestSeq, 41);
      final resync = parseTaskStreamFrame(
          parse('event: resync\ndata: {"reason":"gap","latest_seq":90}\n\n').single);
      final r = resync.event as TaskStreamResync;
      expect((r.reason, r.latestSeq), ('gap', 90));
      expect(resync.seq, 90);
    });

    test('malformed JSON, non-object data, unknown events and tasks without an id never throw', () {
      for (final wire in [
        'event: task.started\ndata: {not json\n\n',
        'event: task.started\ndata: [1,2]\n\n',
        'event: mystery\ndata: {"seq": 4}\n\n',
        'event: task.started\ndata: {"seq": 5}\n\n', // no task id
        'event: task.started\ndata: {"id":"x"}\n\n', // no seq
      ]) {
        expect(parseTaskStreamFrame(parse(wire).single).event, isNull, reason: wire);
      }
    });

    test('dag.* frames are ignored as events but still report their seq (Last-Event-ID stays current)', () {
      final p = parseTaskStreamFrame(parse('id: 7\nevent: dag.started\ndata: {"seq":7,"task_count":3}\n\n').single);
      expect(p.event, isNull);
      expect(p.seq, 7);
    });

    test('a bad frame still reports the id: line so the position is not lost', () {
      final p = parseTaskStreamFrame(parse('id: 9\nevent: task.failed\ndata: nope\n\n').single);
      expect((p.event, p.seq), (null, 9));
    });
  });

  group('upsertTask', () {
    test('replaces by id and keeps newest-first order', () {
      var list = <Task>[];
      list = upsertTask(list, mk('old', ageMin: 30));
      list = upsertTask(list, mk('new', ageMin: 1));
      list = upsertTask(list, mk('mid', ageMin: 10));
      expect(list.map((t) => t.id), ['new', 'mid', 'old']);
      list = upsertTask(list, mk('mid', ageMin: 10, status: TaskStatus.completed));
      expect(list.map((t) => t.id), ['new', 'mid', 'old']);
      expect(list[1].status, TaskStatus.completed);
    });

    test('is pure and deterministic on ties', () {
      final base = [mk('b'), mk('a')];
      final out = upsertTask(base, mk('c'));
      expect(base.length, 2);
      expect(out.map((t) => t.id), ['a', 'b', 'c']);
    });
  });

  group('Task control flags (contract B)', () {
    Map<String, dynamic> json(Map<String, dynamic> extra) =>
        {'id': 'x', 'title': 't', 'status': 'running', ...extra};

    test('parsed from the server', () {
      final t = Task.fromJson(json({
        'controls': {'pause': false, 'cancel': 'best_effort'},
        'can_pause': false, 'can_resume': false, 'can_cancel': true,
        'cancel_outcome': 'abandoned', 'control_blocked_reason': 'Atomic call',
      }));
      expect((t.canPause, t.canResume, t.canCancel), (false, false, true));
      expect(t.controls!.cancel, TaskCancelMode.bestEffort);
      expect(t.cancelOutcome, TaskCancelOutcome.abandoned);
      expect(t.pauseBlocked, isTrue);
      expect(t.controlBlockedReason, 'Atomic call');
    });

    test('an older server (no flags) falls back to the status rules', () {
      expect(Task.fromJson(json({})).canPause, isTrue);
      expect(Task.fromJson(json({'status': 'paused'})).canResume, isTrue);
      expect(Task.fromJson(json({'status': 'completed'})).canCancel, isFalse);
      expect(Task.fromJson(json({'status': 'queued'})).canCancel, isTrue);
    });

    test('unknown values are tolerated', () {
      final t = Task.fromJson(json({
        'controls': {'pause': true, 'cancel': 'quantum'},
        'cancel_outcome': 'who-knows',
      }));
      expect(t.controls!.cancel, TaskCancelMode.bestEffort);
      expect(t.cancelOutcome, isNull);
    });

    test('toJson round-trips the flags', () {
      final t = Task.fromJson(json({
        'can_pause': false, 'can_cancel': true, 'cancel_outcome': 'not_started',
        'controls': {'pause': false, 'cancel': 'none'},
      }));
      final again = Task.fromJson(t.toJson());
      expect((again.canPause, again.canCancel, again.cancelOutcome), (false, true, TaskCancelOutcome.notStarted));
      expect(again.controls!.cancel, TaskCancelMode.none);
    });
  });

  group('TaskService control errors (HTTP 409)', () {
    Future<Object?> call(int status, Object body) async {
      final mock = MockClient((_) async => http.Response(jsonEncode(body), status,
          headers: {'content-type': 'application/json'}));
      Object? thrown;
      await http.runWithClient(() async {
        try {
          await TaskService(fakeApiForTest()).pause('t1');
        } catch (e) {
          thrown = e;
        }
      }, () => mock);
      return thrown;
    }

    test('409 becomes TaskControlException with the server message and code', () async {
      final e = await call(409, {
        'detail': {'code': 'control_not_supported', 'message': 'This plugin cannot be paused.'}
      }) as TaskControlException;
      expect((e.code, e.message), ('control_not_supported', 'This plugin cannot be paused.'));
    });

    test('a plain-string detail is tolerated', () async {
      final e = await call(409, {'detail': 'nope'}) as TaskControlException;
      expect((e.code, e.message), ('invalid_state', 'nope'));
    });

    test('other errors pass through unchanged', () async {
      expect(await call(500, {'detail': 'boom'}), isA<ApiException>());
    });
  });
}

ApiClient fakeApiForTest() => ApiClient(baseUrl: 'http://backend.test', apiPrefix: '/api/v1');
