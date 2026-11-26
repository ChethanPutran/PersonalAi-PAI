import 'dart:async';
import 'dart:convert';

import 'package:pai_mobile/chat/sse_client.dart';
import 'package:pai_mobile/tasks/task.dart';
import 'package:pai_mobile/tasks/task_event_stream.dart';
import 'package:pai_mobile/tasks/task_events.dart';
import 'package:pai_mobile/tasks/task_service.dart';

import '../ui/fakes.dart';

final DateTime t0 = DateTime(2026, 11, 23, 12, 0, 0);

/// A task with sensible defaults; `ageMin` controls createdAt so ordering is explicit.
Task mk(
  String id, {
  String? title,
  TaskStatus status = TaskStatus.running,
  int ageMin = 0,
  bool? canPause,
  bool? canResume,
  bool? canCancel,
  TaskControls? controls,
  TaskCancelOutcome? cancelOutcome,
  String? blockedReason,
  String? error,
  TaskErrorInfo? errorInfo,
  TaskRetry? retry,
}) =>
    Task(
      id: id,
      title: title ?? 'Task $id',
      status: status,
      capability: 'media.control',
      deviceId: 'pixel-8',
      createdAt: t0.subtract(Duration(minutes: ageMin)),
      canPause: canPause,
      canResume: canResume,
      canCancel: canCancel,
      controls: controls,
      cancelOutcome: cancelOutcome,
      controlBlockedReason: blockedReason,
      error: error,
      errorInfo: errorInfo,
      retry: retry,
    );

/// The wire JSON of a task event frame (what the server sends in `data:`).
Map<String, dynamic> eventJson(Task t, {required int seq, required String event}) =>
    {...t.toJson(), 'seq': seq, 'event': event};

SseEvent frame(String event, Map<String, dynamic> data, {String? id}) =>
    SseEvent(event: event, data: jsonEncode(data), id: id);

TaskUpdated updated(Task t, {int seq = 1, String event = 'task.updated'}) =>
    TaskUpdated(seq: seq, event: event, task: t);

/// A live feed the test drives by hand.
class FakeLiveFeed implements TaskLiveFeed {
  final _events = StreamController<TaskStreamEvent>.broadcast();
  final _conn = StreamController<TaskStreamConnection>.broadcast();
  TaskStreamConnection _state = TaskStreamConnection.connected;

  @override
  Stream<TaskStreamEvent> get events => _events.stream;
  @override
  Stream<TaskStreamConnection> get connection => _conn.stream;
  @override
  TaskStreamConnection get state => _state;

  void emit(TaskStreamEvent e) => _events.add(e);
  void setState(TaskStreamConnection s) {
    _state = s;
    _conn.add(s);
  }

  Future<void> close() async {
    await _events.close();
    await _conn.close();
  }
}

/// Task service that counts fetches and lets a test script control calls.
class CountingTaskService extends TaskService {
  CountingTaskService(this.items) : super(fakeApi());

  List<Task> items;
  int listCalls = 0;
  int getCalls = 0;
  Completer<List<Task>>? gate; // when set, getTasks waits for it
  final calls = <String>[]; // 'pause:t1', 'cancel:t2' …
  Object? controlError; // thrown by pause/resume/cancel

  @override
  Future<List<Task>> getTasks() async {
    listCalls++;
    if (gate != null) return gate!.future;
    return List.of(items);
  }

  @override
  Future<Task> getTask(String id) async {
    getCalls++;
    return items.firstWhere((t) => t.id == id);
  }

  Future<void> _ctl(String what, String id) async {
    calls.add('$what:$id');
    if (controlError != null) throw controlError!;
  }

  @override
  Future<void> pause(String id) => _ctl('pause', id);
  @override
  Future<void> resume(String id) => _ctl('resume', id);
  @override
  Future<void> cancel(String id) => _ctl('cancel', id);
}

/// SSE client whose connections the test controls. Every `get` consumes the next [Conn];
/// when none is left the connection simply hangs open (like a quiet real feed).
class ScriptedSseClient extends SseClient {
  ScriptedSseClient();

  final List<Conn> _queue = [];
  final List<Map<String, String>> requests = []; // headers of each connect attempt
  int get attempts => requests.length;

  Conn enqueue([Conn? c]) {
    final conn = c ?? Conn();
    _queue.add(conn);
    return conn;
  }

  @override
  Stream<SseEvent> get(Uri url,
      {required Map<String, String> headers, void Function()? onOpen, void Function()? onActivity}) {
    requests.add(Map.of(headers));
    final conn = _queue.isNotEmpty ? _queue.removeAt(0) : Conn();
    return conn.open(onOpen, onActivity);
  }
}

class Conn {
  Conn({this.failWith});

  /// Throw this instead of opening (e.g. `SseHttpException(401, '')`).
  final Object? failWith;

  late final StreamController<SseEvent> _c = StreamController<SseEvent>(onCancel: () => cancelled = true);
  bool cancelled = false;
  bool opened = false;

  void Function()? _onActivity;

  Stream<SseEvent> open(void Function()? onOpen, [void Function()? onActivity]) async* {
    if (failWith != null) throw failWith!;
    opened = true;
    _onActivity = onActivity;
    onOpen?.call();
    yield* _c.stream;
  }

  /// A `: keep-alive` comment: bytes arrive, but no event.
  void heartbeat() => _onActivity?.call();

  void send(SseEvent e) {
    _onActivity?.call();
    _c.add(e);
  }
  Future<void> close() => _c.close(); // server closes the stream
  void error(Object e) => _c.addError(e);
}
