// lib/tasks/task_events.dart
//
// Typed view of the per-user task feed (`GET /api/v1/tasks/events`, Server-Sent Events).
//
// Wire contract:
//   id: <seq>                     event: task.started   data: {<task JSON>, "seq": 12, "event": "task.started"}
//   event: ready                  data: {"latest_seq": 11}          (first connect)
//   event: resync                 data: {"reason": "gap", "latest_seq": 40}  (could not replay)
//
// A task event carries the WHOLE task, so the UI upserts it straight into its list without any
// extra REST call.

import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../chat/sse_client.dart';
import 'task.dart';

/// Health of the live feed, as the UI should show it.
///  * connected    — frames are flowing;
///  * reconnecting — the last attempt dropped/failed; retrying with backoff (silent);
///  * offline      — repeated failures (or the server rejected us); UI shows a calm notice.
enum TaskStreamConnection { connected, reconnecting, offline }

sealed class TaskStreamEvent {
  const TaskStreamEvent();
}

/// A task was created or changed. [task] is its full current state.
class TaskUpdated extends TaskStreamEvent {
  final int seq;
  final String event; // e.g. "task.started"
  final Task task;
  const TaskUpdated({required this.seq, required this.event, required this.task});

  @override
  String toString() => 'TaskUpdated(#$seq $event ${task.id} ${task.status.name})';
}

/// First frame of a fresh connection.
class TaskStreamReady extends TaskStreamEvent {
  final int latestSeq;
  const TaskStreamReady(this.latestSeq);
}

/// The server could not replay what we missed (or restarted): reload the list ONCE.
class TaskStreamResync extends TaskStreamEvent {
  final String reason;
  final int latestSeq;
  const TaskStreamResync({required this.reason, required this.latestSeq});

  @override
  String toString() => 'TaskStreamResync($reason, latest=$latestSeq)';
}

/// What [parseTaskStreamFrame] returns: the typed event (if any) and the sequence number the
/// frame carried (so even ignored frames, e.g. `dag.*`, advance `Last-Event-ID`).
class ParsedFrame {
  final TaskStreamEvent? event;
  final int? seq;
  const ParsedFrame(this.event, this.seq);
}

/// Tolerant parser: unknown events and malformed JSON never throw — they yield no event.
ParsedFrame parseTaskStreamFrame(SseEvent frame) {
  Map<String, dynamic>? data;
  try {
    final decoded = jsonDecode(frame.data);
    if (decoded is Map) data = Map<String, dynamic>.from(decoded);
  } catch (e) {
    debugPrint('[TaskEvents] ignoring frame "${frame.event}" with bad JSON: $e');
    return ParsedFrame(null, _asInt(frame.id));
  }
  if (data == null) return ParsedFrame(null, _asInt(frame.id));

  final seq = _asInt(data['seq']) ?? _asInt(frame.id);

  switch (frame.event) {
    case 'ready':
      final latest = _asInt(data['latest_seq']);
      return ParsedFrame(latest == null ? null : TaskStreamReady(latest), latest);
    case 'resync':
      final latest = _asInt(data['latest_seq']) ?? 0;
      return ParsedFrame(
        TaskStreamResync(reason: data['reason']?.toString() ?? 'gap', latestSeq: latest),
        latest,
      );
  }

  // Only `task.*` frames carry a task. `dag.*` (and anything unknown) are ignored here.
  if (!frame.event.startsWith('task.') || seq == null) return ParsedFrame(null, seq);
  final id = data['id']?.toString() ?? '';
  if (id.isEmpty) return ParsedFrame(null, seq);
  try {
    return ParsedFrame(
      TaskUpdated(seq: seq, event: frame.event, task: Task.fromJson(data)),
      seq,
    );
  } catch (e, st) {
    debugPrint('[TaskEvents] ignoring unparsable ${frame.event} #$seq: $e');
    debugPrintStack(stackTrace: st, label: '[TaskEvents] parse', maxFrames: 4);
    return ParsedFrame(null, seq);
  }
}

int? _asInt(dynamic v) {
  if (v == null) return null;
  if (v is int) return v;
  if (v is num) return v.toInt();
  return int.tryParse(v.toString());
}

/// Newest first (`createdAt` desc), ties broken by id so the order is deterministic.
List<Task> sortTasks(Iterable<Task> tasks) {
  final out = List<Task>.of(tasks);
  out.sort((a, b) {
    final c = b.createdAt.compareTo(a.createdAt);
    return c != 0 ? c : a.id.compareTo(b.id);
  });
  return out;
}

/// Upserts [incoming] into [current] (replace by id, else insert) and keeps the list sorted by
/// `createdAt`, newest first. Pure: returns a new list.
List<Task> upsertTask(List<Task> current, Task incoming) {
  final out = List<Task>.of(current);
  final i = out.indexWhere((t) => t.id == incoming.id);
  if (i >= 0) {
    out[i] = incoming;
  } else {
    out.add(incoming);
  }
  return sortTasks(out);
}
