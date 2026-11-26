import 'dart:async';
import 'dart:math';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/chat/sse_client.dart';
import 'package:pai_mobile/tasks/task.dart';
import 'package:pai_mobile/tasks/task_event_stream.dart';
import 'package:pai_mobile/tasks/task_events.dart';

import 'helpers.dart';

/// Jitter fixed at x1.0 so the schedule is exact.
class _Mid implements Random {
  @override
  bool nextBool() => true;
  @override
  double nextDouble() => 0.5;
  @override
  int nextInt(int max) => 0;
}

class Rig {
  Rig({
    Duration stableAfter = const Duration(seconds: 10),
    Duration idleTimeout = const Duration(hours: 1),      // off unless a test turns it on
    Duration idleCheckEvery = const Duration(hours: 1),
  }) {
    stream = TaskEventStream(
      url: Uri.parse('http://backend.test/api/v1/tasks/events'),
      headers: () => {'Authorization': 'Bearer secret-token', 'Accept': 'application/json'},
      onUnauthorized: () => unauthorized++,
      client: client,
      random: _Mid(),
      stableAfter: stableAfter,
      idleTimeout: idleTimeout,
      idleCheckEvery: idleCheckEvery,
      now: () => clock,
      delay: (d) {
        delays.add(d);
        return Future<void>.delayed(Duration.zero); // instant, but recorded
      },
    );
    events = [];
    states = [];
    stream.events.listen(events.add);
    stream.connection.listen(states.add);
  }

  final client = ScriptedSseClient();
  late final TaskEventStream stream;
  final delays = <Duration>[];
  var unauthorized = 0;
  var clock = DateTime(2026, 11, 23, 12);
  late final List<TaskStreamEvent> events;
  late final List<TaskStreamConnection> states;

  Future<void> settle() => pumpEventQueue(times: 40);
  void tick(Duration d) => clock = clock.add(d);
}

void main() {
  test('connects without Last-Event-ID and reports connected once the server accepts', () async {
    final r = Rig();
    final c = r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();

    expect(r.client.attempts, 1);
    expect(r.client.requests.single.containsKey('Last-Event-ID'), isFalse);
    expect(r.stream.state, TaskStreamConnection.connected);
    expect(c.opened, isTrue);
    await r.stream.dispose();
  });

  test('emits typed events and tracks the last seq (dag.* frames advance it too)', () async {
    final r = Rig();
    final c = r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();

    c.send(frame('ready', {'latest_seq': 10}));
    c.send(frame('task.created', eventJson(mk('a'), seq: 11, event: 'task.created'), id: '11'));
    c.send(frame('dag.started', {'seq': 12, 'task_count': 2}, id: '12'));
    c.send(frame('task.completed', eventJson(mk('a', status: TaskStatus.completed), seq: 13, event: 'task.completed'), id: '13'));
    await r.settle();

    expect(r.events.map((e) => e.runtimeType.toString()), ['TaskStreamReady', 'TaskUpdated', 'TaskUpdated']);
    expect((r.events.last as TaskUpdated).task.status, TaskStatus.completed);
    expect(r.stream.lastSeq, 13);
    await r.stream.dispose();
  });

  test('after a drop it reconnects sending Last-Event-ID = the last seq seen', () async {
    final r = Rig();
    final first = r.client.enqueue();
    r.client.enqueue(); // second connection: stays open and quiet
    r.stream.start(observeLifecycle: false);
    await r.settle();

    first.send(frame('task.started', eventJson(mk('a'), seq: 5, event: 'task.started'), id: '5'));
    first.send(frame('task.completed', eventJson(mk('a', status: TaskStatus.completed), seq: 6, event: 'task.completed'), id: '6'));
    await r.settle();
    await first.close(); // network blip
    await r.settle();

    expect(r.client.attempts, 2);
    expect(r.client.requests[1]['Last-Event-ID'], '6');
    expect(r.stream.state, TaskStreamConnection.connected); // second connection accepted
    await r.stream.dispose();
  });

  test('a replayed duplicate (seq <= last) is dropped, newer ones pass', () async {
    final r = Rig();
    final c = r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();
    c.send(frame('task.started', eventJson(mk('a'), seq: 8, event: 'task.started'), id: '8'));
    c.send(frame('task.started', eventJson(mk('a'), seq: 8, event: 'task.started'), id: '8'));
    c.send(frame('task.started', eventJson(mk('a'), seq: 7, event: 'task.started'), id: '7'));
    c.send(frame('task.completed', eventJson(mk('a'), seq: 9, event: 'task.completed'), id: '9'));
    await r.settle();
    expect(r.events.whereType<TaskUpdated>().map((e) => e.seq), [8, 9]);
    await r.stream.dispose();
  });

  test('backoff doubles 1s,2s,4s… up to a 30s cap (±jitter fixed here), never a tight loop', () async {
    final r = Rig();
    // eight connections that fail instantly with a 503, then one that stays open
    for (var i = 0; i < 8; i++) {
      r.client.enqueue(Conn(failWith: SseHttpException(503, 'down')));
    }
    r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();
    await r.settle();

    expect(r.delays.map((d) => d.inSeconds), [1, 2, 4, 8, 16, 30, 30, 30]);
    expect(r.stream.state, TaskStreamConnection.connected);
    expect(r.client.attempts, 9);
    await r.stream.dispose();
  });

  test('state: connected -> reconnecting after a drop -> offline when the retry also fails -> connected', () async {
    final r = Rig();
    final first = r.client.enqueue();
    r.client.enqueue(Conn(failWith: SseHttpException(503, 'x')));
    r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();
    expect(r.states, [TaskStreamConnection.connected]);

    await first.close();
    await r.settle();
    await r.settle();
    expect(r.states, [
      TaskStreamConnection.connected,
      TaskStreamConnection.reconnecting, // first miss: silent retry
      TaskStreamConnection.offline, // second miss: the UI shows its notice
      TaskStreamConnection.connected,
    ]);
    await r.stream.dispose();
  });

  test('a connection that stayed up long enough resets the backoff', () async {
    final r = Rig();
    final long = r.client.enqueue();
    r.client.enqueue(Conn(failWith: SseHttpException(503, 'x')));
    r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();

    r.tick(const Duration(minutes: 5)); // the first connection lived 5 minutes
    await long.close();
    await r.settle();
    r.tick(const Duration(seconds: 1));
    await r.settle();

    // After the long-lived drop the wait is the FIRST step (1s), not escalated.
    expect(r.delays.first, const Duration(seconds: 1));
    await r.stream.dispose();
  });

  test('flapping server (opens then closes at once) does not reset the backoff', () async {
    final r = Rig();
    for (var i = 0; i < 4; i++) {
      final c = r.client.enqueue();
      unawaited(Future<void>.delayed(Duration.zero).then((_) => c.close()));
    }
    r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();
    await r.settle();
    expect(r.delays.take(4).map((d) => d.inSeconds), [1, 2, 4, 8]);
    await r.stream.dispose();
  });

  test('resync from the server is emitted and the position jumps to latest_seq', () async {
    final r = Rig();
    final c = r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();
    c.send(frame('resync', {'reason': 'gap', 'latest_seq': 70}));
    await r.settle();

    final resync = r.events.whereType<TaskStreamResync>().single;
    expect((resync.reason, resync.latestSeq), ('gap', 70));
    expect(r.stream.lastSeq, 70);
    await r.stream.dispose();
  });

  test('a server whose counter went backwards (restart) triggers a resync', () async {
    final r = Rig();
    final first = r.client.enqueue();
    final second = r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();
    first.send(frame('task.started', eventJson(mk('a'), seq: 500, event: 'task.started'), id: '500'));
    await r.settle();
    await first.close();
    await r.settle();

    second.send(frame('ready', {'latest_seq': 3})); // server restarted
    await r.settle();

    expect(r.events.whereType<TaskStreamResync>().single.reason, 'server_restart');
    expect(r.stream.lastSeq, 3);
    await r.stream.dispose();
  });

  test('401 hands off to the unauthorized flow once and stops retrying for good', () async {
    final r = Rig();
    r.client.enqueue(Conn(failWith: SseHttpException(401, 'expired')));
    r.client.enqueue(); // must never be used
    r.stream.start(observeLifecycle: false);
    await r.settle();
    await r.settle();

    expect(r.unauthorized, 1);
    expect(r.client.attempts, 1);
    expect(r.delays, isEmpty);
    expect(r.stream.state, TaskStreamConnection.offline);
    await r.stream.dispose();
  });

  test('404 (server has no feed) is not retried — that would just be slow polling', () async {
    final r = Rig();
    r.client.enqueue(Conn(failWith: SseHttpException(404, 'Not Found')));
    r.stream.start(observeLifecycle: false);
    await r.settle();
    await r.settle();
    expect(r.client.attempts, 1);
    expect(r.stream.state, TaskStreamConnection.offline);
    expect(r.unauthorized, 0);
    await r.stream.dispose();
  });

  group('app lifecycle', () {
    test('background drops the connection; foreground resumes from the last seq', () async {
      final r = Rig();
      final first = r.client.enqueue();
      r.client.enqueue();
      r.stream.start(observeLifecycle: false);
      await r.settle();
      first.send(frame('task.started', eventJson(mk('a'), seq: 21, event: 'task.started'), id: '21'));
      await r.settle();

      r.stream.didChangeAppLifecycleState(AppLifecycleState.paused);
      await r.settle();
      expect(first.cancelled, isTrue); // socket really closed
      expect(r.stream.state, TaskStreamConnection.offline);
      expect(r.client.attempts, 1); // and nothing reconnects in the background
      await r.settle();
      expect(r.client.attempts, 1);

      r.stream.didChangeAppLifecycleState(AppLifecycleState.resumed);
      await r.settle();
      expect(r.client.attempts, 2);
      expect(r.client.requests[1]['Last-Event-ID'], '21');
      expect(r.stream.state, TaskStreamConnection.connected);
      await r.stream.dispose();
    });

    test('inactive (e.g. notification shade) keeps the feed', () async {
      final r = Rig();
      final c = r.client.enqueue();
      r.stream.start(observeLifecycle: false);
      await r.settle();
      r.stream.didChangeAppLifecycleState(AppLifecycleState.inactive);
      await r.settle();
      expect(c.cancelled, isFalse);
      expect(r.stream.state, TaskStreamConnection.connected);
      await r.stream.dispose();
    });

    test('stop() closes the connection and nothing ever reconnects', () async {
      final r = Rig();
      final c = r.client.enqueue();
      r.client.enqueue();
      r.stream.start(observeLifecycle: false);
      await r.settle();
      r.stream.stop();
      await r.settle();
      expect(c.cancelled, isTrue);
      expect(r.client.attempts, 1);
      await r.stream.dispose();
    });
  });

  test('a malformed frame in the middle of the feed does not break it', () async {
    final r = Rig();
    final c = r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();
    c.send(const SseEvent(event: 'task.started', data: '{broken', id: '1'));
    c.send(frame('task.started', eventJson(mk('a'), seq: 2, event: 'task.started'), id: '2'));
    await r.settle();
    expect(r.events.whereType<TaskUpdated>().single.seq, 2);
    await r.stream.dispose();
  });

  group('liveness watchdog (a connection that dies silently)', () {
    Rig watched() => Rig(idleTimeout: const Duration(seconds: 45), idleCheckEvery: const Duration(milliseconds: 5));

    // Real (tiny) timer ticks drive the check; the fake clock decides how long it has been quiet.
    Future<void> checks() => Future<void>.delayed(const Duration(milliseconds: 40));

    test('no bytes for longer than the timeout: drops the connection and reconnects with Last-Event-ID', () async {
      final r = watched();
      final first = r.client.enqueue();
      final second = r.client.enqueue();
      r.stream.start(observeLifecycle: false);
      await r.settle();
      first.send(frame('ready', {'latest_seq': 10}));
      first.send(frame('task.created', eventJson(mk('a'), seq: 11, event: 'task.created'), id: '11'));
      await r.settle();
      expect(r.client.attempts, 1);

      r.tick(const Duration(seconds: 50)); // silence, no error, no close: the connection is simply dead
      await checks();
      await r.settle();

      expect(first.cancelled, isTrue);
      expect(r.client.attempts, 2);
      expect(r.client.requests.last['Last-Event-ID'], '11'); // replay from where it left off

      // The new connection delivers what the dead one never did.
      second.send(frame('task.completed',
          eventJson(mk('a', status: TaskStatus.completed), seq: 12, event: 'task.completed'), id: '12'));
      await r.settle();
      expect(r.events.whereType<TaskUpdated>().last.task.status, TaskStatus.completed);
      await r.stream.dispose();
    });

    test('keep-alive comments count as life: a quiet but healthy feed is left alone', () async {
      final r = watched();
      final c = r.client.enqueue();
      r.stream.start(observeLifecycle: false);
      await r.settle();
      for (var i = 0; i < 4; i++) {
        r.tick(const Duration(seconds: 15));
        c.heartbeat(); // ": keep-alive" every 15 s
        await checks();
      }
      expect(r.client.attempts, 1);
      expect(c.cancelled, isFalse);
      await r.stream.dispose();
    });

    test('a frame also resets the timer', () async {
      final r = watched();
      final c = r.client.enqueue();
      r.stream.start(observeLifecycle: false);
      await r.settle();
      r.tick(const Duration(seconds: 40));
      c.send(frame('ready', {'latest_seq': 0}));
      r.tick(const Duration(seconds: 40));
      await checks();
      expect(r.client.attempts, 1);
      await r.stream.dispose();
    });
  });

  test('requestRefresh asks the list to reload once (a push says a schedule fired)', () async {
    final r = Rig();
    r.client.enqueue();
    r.stream.start(observeLifecycle: false);
    await r.settle();
    r.stream.requestRefresh('schedule_fired');
    await r.settle();
    final resyncs = r.events.whereType<TaskStreamResync>().toList();
    expect(resyncs.length, 1);
    expect(resyncs.single.reason, 'schedule_fired');
    await r.stream.dispose();
    r.stream.requestRefresh('after_stop'); // a stopped feed stays quiet
  });
}
