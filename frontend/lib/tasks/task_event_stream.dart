// lib/tasks/task_event_stream.dart
//
// ONE long-lived Server-Sent-Events connection to `GET /api/v1/tasks/events` for the signed-in
// user. The Tasks screens apply what arrives; nothing polls.
//
//  * resumes gap-free after a drop by sending `Last-Event-ID: <last seq seen>`;
//  * reconnects with exponential backoff + jitter (1 s → 30 s) — never a tight loop;
//  * disconnects when the app is backgrounded and resumes when it returns;
//  * 401 → hands off to the app's unauthorized flow and stops (no retry storm);
//  * logs connect / disconnect / seq numbers with debugPrint — never the token.

import 'dart:async';
import 'dart:math';

import 'package:flutter/widgets.dart';

import '../chat/sse_client.dart';
import 'task_events.dart';

/// What the Tasks screens depend on. [TaskEventStream] is the real one; tests use a fake.
abstract class TaskLiveFeed {
  Stream<TaskStreamEvent> get events;
  Stream<TaskStreamConnection> get connection;

  /// Current state (so a screen opened later doesn't have to wait for the next change).
  TaskStreamConnection get state;
}

class TaskEventStream with WidgetsBindingObserver implements TaskLiveFeed {
  TaskEventStream({
    required this.url,
    required this.headers,
    this.onUnauthorized,
    SseClient? client,
    this.minRetry = const Duration(seconds: 1),
    this.maxRetry = const Duration(seconds: 30),
    this.stableAfter = const Duration(seconds: 10),
    this.idleTimeout = const Duration(seconds: 45),
    this.idleCheckEvery = const Duration(seconds: 5),
    Random? random,
    Future<void> Function(Duration)? delay,
    DateTime Function()? now,
  })  : _client = client ?? SseClient(),
        _random = random ?? Random(),
        _delay = delay ?? Future<void>.delayed,
        _now = now ?? DateTime.now;

  /// `.../api/v1/tasks/events`
  final Uri url;

  /// Auth + content headers, read fresh on every (re)connect (the token can change).
  final Map<String, String> Function() headers;

  /// Called on HTTP 401: the session is dead. The stream stops retrying.
  final VoidCallback? onUnauthorized;

  final Duration minRetry;
  final Duration maxRetry;

  /// A connection that stayed up this long counts as healthy: the backoff starts over.
  final Duration stableAfter;

  /// The server sends a keep-alive every 15 s, so no bytes at all for this long means the
  /// connection is dead without having said so (see [SseClient.get]'s `onActivity`): drop it and
  /// reconnect, replaying what was missed from `Last-Event-ID`. Without this the feed stayed
  /// "connected" forever after such a drop and new tasks only showed up on a manual refresh.
  final Duration idleTimeout;
  final Duration idleCheckEvery;

  final SseClient _client;
  final Random _random;
  final Future<void> Function(Duration) _delay;
  final DateTime Function() _now;

  static const _tag = '[TaskEvents]';

  final _events = StreamController<TaskStreamEvent>.broadcast();
  final _connection = StreamController<TaskStreamConnection>.broadcast();

  TaskStreamConnection _state = TaskStreamConnection.reconnecting;
  int? _lastSeq;
  int _failures = 0;
  int _generation = 0; // bumped on stop/suspend so an old loop notices and exits
  bool _started = false;
  bool _suspended = false;
  bool _lifecycleObserved = false;
  _Attempt? _attempt;

  @override
  Stream<TaskStreamEvent> get events => _events.stream;

  @override
  Stream<TaskStreamConnection> get connection => _connection.stream;

  @override
  TaskStreamConnection get state => _state;

  /// Last event sequence number seen (what `Last-Event-ID` will carry).
  int? get lastSeq => _lastSeq;

  // ------------------------------------------------------------------
  // Lifecycle
  // ------------------------------------------------------------------

  /// Begin streaming. [observeLifecycle] hooks app foreground/background (off in unit tests).
  void start({bool observeLifecycle = true}) {
    if (_started) return;
    _started = true;
    _suspended = false;
    if (observeLifecycle) {
      WidgetsBinding.instance.addObserver(this);
      _lifecycleObserved = true;
    }
    _launch();
  }

  /// Stop for good (sign-out). Safe to call twice.
  void stop() {
    if (!_started) return;
    _started = false;
    _generation++;
    _attempt?.cancel();
    if (_lifecycleObserved) {
      WidgetsBinding.instance.removeObserver(this);
      _lifecycleObserved = false;
    }
    debugPrint('$_tag stopped');
  }

  Future<void> dispose() async {
    stop();
    await _events.close();
    await _connection.close();
  }

  /// Ask whoever shows the task list to reload it once (e.g. a push says a schedule just fired).
  /// A second path to the same freshness as the live feed, for when the feed missed something.
  void requestRefresh(String reason) {
    if (!_started) return;
    _emit(TaskStreamResync(reason: reason, latestSeq: _lastSeq ?? 0));
  }

  /// App went to the background: drop the connection (no battery drain, no stale socket).
  void suspend() {
    if (!_started || _suspended) return;
    _suspended = true;
    _generation++;
    _attempt?.cancel();
    debugPrint('$_tag suspended (app in background); last seq=$_lastSeq');
    _set(TaskStreamConnection.offline);
  }

  /// App is back: reconnect right away and resume from the last seq.
  void resume() {
    if (!_started || !_suspended) return;
    _suspended = false;
    _failures = 0;
    debugPrint('$_tag resuming from seq=$_lastSeq');
    _set(TaskStreamConnection.reconnecting);
    _launch();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.paused:
      case AppLifecycleState.detached:
      case AppLifecycleState.hidden:
        suspend();
      case AppLifecycleState.resumed:
        resume();
      case AppLifecycleState.inactive:
        break; // e.g. the notification shade: still visible, keep the feed
    }
  }

  // ------------------------------------------------------------------
  // Connection loop
  // ------------------------------------------------------------------

  void _launch() => unawaited(_loop(++_generation));

  Future<void> _loop(int gen) async {
    while (_started && !_suspended && gen == _generation) {
      final attempt = _Attempt();
      _attempt = attempt;
      final startedAt = _now();
      String reason;
      var retry = true;

      try {
        debugPrint('$_tag connecting (Last-Event-ID=${_lastSeq ?? "-"})');
        reason = await _connectOnce(attempt, gen);
      } on SseHttpException catch (e) {
        reason = 'HTTP ${e.statusCode}';
        if (e.statusCode == 401) {
          debugPrint('$_tag server rejected our session (401) — stopping, no retries');
          _set(TaskStreamConnection.offline);
          onUnauthorized?.call();
          return;
        }
        if (e.statusCode == 403 || e.statusCode == 404 || e.statusCode == 405 || e.statusCode == 501) {
          // Not retryable: this server doesn't offer the feed (or we may not use it). Retrying
          // would just be slow polling. A resume from background tries once more.
          debugPrint('$_tag feed unavailable (HTTP ${e.statusCode}) — not retrying: ${_clip(e.body)}');
          _set(TaskStreamConnection.offline);
          retry = false;
        } else {
          debugPrint('$_tag HTTP ${e.statusCode}: ${_clip(e.body)}');
        }
      } catch (e, st) {
        reason = '${e.runtimeType}: $e';
        debugPrint('$_tag connection error: $reason');
        debugPrintStack(stackTrace: st, label: _tag, maxFrames: 4);
      }

      if (gen != _generation || !_started || _suspended) return; // superseded: say nothing more
      if (!retry) return;

      final upFor = _now().difference(startedAt);
      if (attempt.opened && upFor >= stableAfter) _failures = 0; // it was healthy
      _failures++;
      final wait = backoffFor(_failures);
      _set(_failures >= 2 ? TaskStreamConnection.offline : TaskStreamConnection.reconnecting);
      debugPrint('$_tag disconnected ($reason) after ${upFor.inSeconds}s; '
          'retry #$_failures in ${wait.inMilliseconds} ms');

      await _delay(wait);
    }
  }

  /// Backoff for the n-th consecutive failure: 1 s, 2 s, 4 s … capped at [maxRetry], ±20% jitter.
  @visibleForTesting
  Duration backoffFor(int failures) {
    final exp = minRetry.inMilliseconds * pow(2, max(0, failures - 1));
    final capped = min(exp.toDouble(), maxRetry.inMilliseconds.toDouble());
    final jitter = 0.8 + 0.4 * _random.nextDouble();
    return Duration(milliseconds: (capped * jitter).round());
  }

  /// One connection. Completes with the reason it ended; throws if it never opened.
  Future<String> _connectOnce(_Attempt attempt, int gen) {
    final done = Completer<String>();
    attempt.onCancel = () {
      if (!done.isCompleted) done.complete('cancelled');
    };

    final lastSeq = _lastSeq;
    var lastActivity = _now();
    final sub = _client
        .get(
          url,
          headers: {...headers(), if (lastSeq != null) 'Last-Event-ID': '$lastSeq'},
          onOpen: () {
            if (gen != _generation) return;
            attempt.opened = true;
            lastActivity = _now();
            debugPrint('$_tag connected');
            _set(TaskStreamConnection.connected);
          },
          onActivity: () => lastActivity = _now(),
        )
        .listen(
          (frame) {
            if (gen == _generation) _onFrame(frame);
          },
          onError: (Object e, StackTrace st) {
            if (!done.isCompleted) done.completeError(e, st);
          },
          onDone: () {
            if (!done.isCompleted) done.complete('server closed the stream');
          },
          cancelOnError: true,
        );
    attempt.subscription = sub;

    // Liveness watchdog (the stream never errors when a mobile connection silently dies).
    final watchdog = Timer.periodic(idleCheckEvery, (_) {
      if (done.isCompleted || gen != _generation || !attempt.opened) return;
      final quiet = _now().difference(lastActivity);
      if (quiet > idleTimeout) {
        debugPrint('$_tag no data for ${quiet.inSeconds}s — connection looks dead, reconnecting');
        done.complete('idle for ${quiet.inSeconds}s');
      }
    });
    return done.future.whenComplete(() {
      watchdog.cancel();
      return sub.cancel();
    });
  }

  void _onFrame(SseEvent frame) {
    final parsed = parseTaskStreamFrame(frame);
    final event = parsed.event;
    final seq = parsed.seq;

    if (event is TaskStreamReady) {
      final known = _lastSeq;
      if (known != null && event.latestSeq < known) {
        // The server's counter went backwards: it restarted and our position is meaningless.
        debugPrint('$_tag server seq ${event.latestSeq} < ours $known → resync');
        _lastSeq = event.latestSeq;
        _emit(TaskStreamResync(reason: 'server_restart', latestSeq: event.latestSeq));
      } else {
        _lastSeq ??= event.latestSeq;
      }
      _emit(event);
      return;
    }
    if (event is TaskStreamResync) {
      debugPrint('$_tag resync requested (${event.reason}); latest=${event.latestSeq}');
      _lastSeq = event.latestSeq;
      _emit(event);
      return;
    }

    if (seq != null) {
      final known = _lastSeq;
      if (known != null && seq <= known) {
        debugPrint('$_tag dropping duplicate/old frame #$seq (have #$known)');
        return;
      }
      _lastSeq = seq;
    }
    if (event != null) {
      debugPrint('$_tag $event');
      _emit(event);
    }
  }

  void _emit(TaskStreamEvent e) {
    if (!_events.isClosed) _events.add(e);
  }

  void _set(TaskStreamConnection s) {
    if (_state == s) return;
    _state = s;
    if (!_connection.isClosed) _connection.add(s);
  }

  static String _clip(String s) => s.length <= 200 ? s : '${s.substring(0, 200)}…';
}

/// Bookkeeping for one connection attempt (so suspend/stop can cancel it mid-flight).
class _Attempt {
  StreamSubscription<SseEvent>? subscription;
  VoidCallback? onCancel;
  bool opened = false;

  void cancel() {
    subscription?.cancel();
    onCancel?.call();
  }
}
