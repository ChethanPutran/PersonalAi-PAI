// lib/tasks/task.dart
//
// Dart mirror of `pai/tasks/models.py`. Field names come through from the
// backend as snake_case; this file exposes camelCase and parses defensively
// so a partial payload (e.g. the slim `/tasks` list response) still
// produces a usable object.

// ---------------------------------------------------------------------------
// Enums — one per backend enum, values match the wire strings exactly
// ---------------------------------------------------------------------------

/// Mirrors `TaskStatus` in `pai/tasks/models.py`.
///
/// Order matters when reading the lifecycle left to right:
///
///   created → initialized → queued → running → completed
///                                       ↓           failed
///                                     paused        cancelled
///                                       ↑
///                                    retrying
enum TaskStatus {
  created,
  initialized,
  queued,
  running,
  paused,
  completed,
  failed,
  cancelled,
  retrying;

  /// Parses the wire value (e.g. `"running"`). Unknown values fall back
  /// to [created] so an added backend status doesn't crash old clients.
  static TaskStatus fromWire(String? wire) {
    if (wire == null || wire.isEmpty) return TaskStatus.created;
    for (final v in TaskStatus.values) {
      if (v.name == wire) return v;
    }
    return TaskStatus.created;
  }

  bool get isTerminal =>
      this == TaskStatus.completed ||
      this == TaskStatus.failed ||
      this == TaskStatus.cancelled;

  bool get isActive =>
      this == TaskStatus.running ||
      this == TaskStatus.paused ||
      this == TaskStatus.queued ||
      this == TaskStatus.retrying;
}


/// Mirrors `TaskPriority`.
enum TaskPriority {
  low,
  normal,
  high,
  critical;

  static TaskPriority fromWire(String? wire) {
    if (wire == null || wire.isEmpty) return TaskPriority.normal;
    for (final v in TaskPriority.values) {
      if (v.name == wire) return v;
    }
    return TaskPriority.normal;
  }
}


/// Mirrors `TaskSource`.
enum TaskSource {
  user,
  agent,
  system,
  plugin,
  scheduled;

  static TaskSource fromWire(String? wire) {
    if (wire == null || wire.isEmpty) return TaskSource.user;
    for (final v in TaskSource.values) {
      if (v.name == wire) return v;
    }
    return TaskSource.user;
  }
}


// ---------------------------------------------------------------------------
// TaskResult
// ---------------------------------------------------------------------------

/// Mirrors `TaskResult` in `pai/tasks/models.py`.
///
/// `output` is deliberately `dynamic` — the backend's `_json_safe()` may
/// produce a Map, a List, a String, or `null`. Callers inspect the runtime
/// type when they need structure.
class TaskResult {
  final bool success;
  final dynamic output;
  final String? error;
  final String? executorId;
  final String? deviceId;
  final DateTime? startedAt;
  final DateTime? completedAt;
  final Map<String, dynamic> metadata;

  const TaskResult({
    required this.success,
    this.output,
    this.error,
    this.executorId,
    this.deviceId,
    this.startedAt,
    this.completedAt,
    this.metadata = const {},
  });

  factory TaskResult.fromJson(Map<String, dynamic> json) {
    return TaskResult(
      success: json['success'] == true,
      output: json['output'],
      error: _strOrNull(json['error']),
      executorId: _strOrNull(json['executor_id']),
      deviceId: _strOrNull(json['device_id']),
      startedAt: _parseDate(json['started_at']),
      completedAt: _parseDate(json['completed_at']),
      metadata:
          (json['metadata'] as Map?)?.cast<String, dynamic>() ?? const {},
    );
  }

  Map<String, dynamic> toJson() => {
        'success': success,
        'output': output,
        'error': error,
        'executor_id': executorId,
        'device_id': deviceId,
        'started_at': startedAt?.toIso8601String(),
        'completed_at': completedAt?.toIso8601String(),
        'metadata': metadata,
      };

  bool get isTerminal => completedAt != null || error != null;
}


// ---------------------------------------------------------------------------
// Task
// ---------------------------------------------------------------------------

/// Mirrors `Task` in `pai/tasks/models.py`.
///
/// The constructor has defaults for every field except [id] and [title],
/// so constructing a `Task` from a slim list response (which may only
/// include id/title/status/device_id/plugin) works without null-checking
/// every field at the call site.
class Task {
  final String id;
  final String title;
  final String type;

  /// Mutable so refresh-in-place is possible without rebuilding the whole
  /// object. Prefer constructing a new `Task` when the source of truth is
  /// the server.
  TaskStatus status;

  final String? userId;
  final String? sessionId;

  final TaskSource source;
  final TaskPriority priority;

  final String input;
  final Map<String, dynamic> parameters;

  final String? capability;
  final String? plugin;
  final String? deviceId;
  final String? executorId;

  final double timeout;
  final int maxRetries;
  final int retryCount;

  final DateTime createdAt;
  final DateTime? initializedAt;
  final DateTime? queuedAt;
  final DateTime? startedAt;
  final DateTime? completedAt;

  final TaskResult? result;
  final String? error;

  final Map<String, dynamic> metadata;

  final String? parentTaskId;
  final List<String> childTaskIds;

  Task({
    required this.id,
    required this.title,
    required this.status,
    this.type = 'task',
    this.userId,
    this.sessionId,
    this.source = TaskSource.user,
    this.priority = TaskPriority.normal,
    this.input = '',
    this.parameters = const {},
    this.capability,
    this.plugin,
    this.deviceId,
    this.executorId,
    this.timeout = 30.0,
    this.maxRetries = 0,
    this.retryCount = 0,
    DateTime? createdAt,
    this.initializedAt,
    this.queuedAt,
    this.startedAt,
    this.completedAt,
    this.result,
    this.error,
    this.metadata = const {},
    this.parentTaskId,
    this.childTaskIds = const [],
  }) : createdAt = createdAt ?? DateTime.now();

  // ------------------------------------------------------------------
  // Derived state
  // ------------------------------------------------------------------

  bool get isTerminal => status.isTerminal;

  bool get canPause => status == TaskStatus.running;

  bool get canResume => status == TaskStatus.paused;

  bool get canCancel => !isTerminal;

  Duration? get duration {
    if (startedAt == null) return null;
    return (completedAt ?? DateTime.now()).difference(startedAt!);
  }

  // ------------------------------------------------------------------
  // Parsing
  // ------------------------------------------------------------------

  factory Task.fromJson(Map<String, dynamic> json) {
    // `result` can arrive in two shapes:
    //   1. A map (backend's `TaskResult.to_dict()`)
    //   2. Nothing / null (task hasn't run yet)
    TaskResult? parsedResult;
    final rawResult = json['result'];
    if (rawResult is Map) {
      parsedResult = TaskResult.fromJson(
        Map<String, dynamic>.from(rawResult),
      );
    } else if (rawResult is String && rawResult.isNotEmpty) {
      // Legacy stringified result — keep the string as `output` so the
      // detail screen still shows something useful instead of null.
      parsedResult = TaskResult(success: true, output: rawResult);
    }

    final rawChildren = json['child_task_ids'];
    final children = rawChildren is List
        ? rawChildren.map((e) => e.toString()).toList(growable: false)
        : const <String>[];

    return Task(
      id: (json['id'] ?? '').toString(),
      title: (json['title'] ?? '(untitled)').toString(),
      status: TaskStatus.fromWire(json['status']?.toString()),
      type: (json['type'] ?? 'task').toString(),
      userId: _strOrNull(json['user_id']),
      sessionId: _strOrNull(json['session_id']),
      source: TaskSource.fromWire(json['source']?.toString()),
      priority: TaskPriority.fromWire(json['priority']?.toString()),
      input: (json['input'] ?? '').toString(),
      parameters:
          (json['parameters'] as Map?)?.cast<String, dynamic>() ?? const {},
      capability: _strOrNull(json['capability']),
      plugin: _strOrNull(json['plugin']),
      deviceId: _strOrNull(json['device_id']),
      executorId: _strOrNull(json['executor_id']),
      timeout: _doubleOr(json['timeout'], 30.0),
      maxRetries: _intOr(json['max_retries'], 0),
      retryCount: _intOr(json['retry_count'], 0),
      createdAt: _parseDate(json['created_at']) ?? DateTime.now(),
      initializedAt: _parseDate(json['initialized_at']),
      queuedAt: _parseDate(json['queued_at']),
      startedAt: _parseDate(json['started_at']),
      completedAt: _parseDate(json['completed_at']),
      result: parsedResult,
      error: _strOrNull(json['error']),
      metadata:
          (json['metadata'] as Map?)?.cast<String, dynamic>() ?? const {},
      parentTaskId: _strOrNull(json['parent_task_id']),
      childTaskIds: children,
    );
  }

  // ------------------------------------------------------------------
  // Serialization
  // ------------------------------------------------------------------

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'status': status.name,
        'type': type,
        'user_id': userId,
        'session_id': sessionId,
        'source': source.name,
        'priority': priority.name,
        'input': input,
        'parameters': parameters,
        'capability': capability,
        'plugin': plugin,
        'device_id': deviceId,
        'executor_id': executorId,
        'timeout': timeout,
        'max_retries': maxRetries,
        'retry_count': retryCount,
        'created_at': createdAt.toIso8601String(),
        'initialized_at': initializedAt?.toIso8601String(),
        'queued_at': queuedAt?.toIso8601String(),
        'started_at': startedAt?.toIso8601String(),
        'completed_at': completedAt?.toIso8601String(),
        'result': result?.toJson(),
        'error': error,
        'metadata': metadata,
        'parent_task_id': parentTaskId,
        'child_task_ids': childTaskIds,
      };

  // ------------------------------------------------------------------
  // Immutable updates
  // ------------------------------------------------------------------

  Task copyWith({
    String? id,
    String? title,
    String? type,
    TaskStatus? status,
    String? userId,
    String? sessionId,
    TaskSource? source,
    TaskPriority? priority,
    String? input,
    Map<String, dynamic>? parameters,
    String? capability,
    String? plugin,
    String? deviceId,
    String? executorId,
    double? timeout,
    int? maxRetries,
    int? retryCount,
    DateTime? createdAt,
    DateTime? initializedAt,
    DateTime? queuedAt,
    DateTime? startedAt,
    DateTime? completedAt,
    TaskResult? result,
    String? error,
    Map<String, dynamic>? metadata,
    String? parentTaskId,
    List<String>? childTaskIds,
  }) {
    return Task(
      id: id ?? this.id,
      title: title ?? this.title,
      type: type ?? this.type,
      status: status ?? this.status,
      userId: userId ?? this.userId,
      sessionId: sessionId ?? this.sessionId,
      source: source ?? this.source,
      priority: priority ?? this.priority,
      input: input ?? this.input,
      parameters: parameters ?? this.parameters,
      capability: capability ?? this.capability,
      plugin: plugin ?? this.plugin,
      deviceId: deviceId ?? this.deviceId,
      executorId: executorId ?? this.executorId,
      timeout: timeout ?? this.timeout,
      maxRetries: maxRetries ?? this.maxRetries,
      retryCount: retryCount ?? this.retryCount,
      createdAt: createdAt ?? this.createdAt,
      initializedAt: initializedAt ?? this.initializedAt,
      queuedAt: queuedAt ?? this.queuedAt,
      startedAt: startedAt ?? this.startedAt,
      completedAt: completedAt ?? this.completedAt,
      result: result ?? this.result,
      error: error ?? this.error,
      metadata: metadata ?? this.metadata,
      parentTaskId: parentTaskId ?? this.parentTaskId,
      childTaskIds: childTaskIds ?? this.childTaskIds,
    );
  }
}


// ---------------------------------------------------------------------------
// Parsing helpers
// ---------------------------------------------------------------------------

String? _strOrNull(dynamic v) {
  if (v == null) return null;
  final s = v.toString();
  return s.isEmpty ? null : s;
}

int _intOr(dynamic v, int fallback) {
  if (v == null) return fallback;
  if (v is int) return v;
  if (v is num) return v.toInt();
  return int.tryParse(v.toString()) ?? fallback;
}

double _doubleOr(dynamic v, double fallback) {
  if (v == null) return fallback;
  if (v is double) return v;
  if (v is num) return v.toDouble();
  return double.tryParse(v.toString()) ?? fallback;
}

/// Accepts the formats the backend produces:
///   - ISO-8601 string (e.g. `"2026-11-07T13:18:59.934821+00:00"`)
///   - epoch seconds (int / double)
///   - `DateTime` (when parsing from an already-parsed source)
///   - `null` / empty → `null`
DateTime? _parseDate(dynamic v) {
  if (v == null) return null;

  if (v is DateTime) return v;

  if (v is int) {
    // Heuristic: values below 10^12 are seconds, above are milliseconds.
    return v < 1000000000000
        ? DateTime.fromMillisecondsSinceEpoch(v * 1000, isUtc: true)
        : DateTime.fromMillisecondsSinceEpoch(v, isUtc: true);
  }

  if (v is double) {
    return DateTime.fromMillisecondsSinceEpoch(
      (v * 1000).round(),
      isUtc: true,
    );
  }

  final s = v.toString().trim();
  if (s.isEmpty) return null;
  try {
    return DateTime.parse(s);
  } catch (_) {
    return null;
  }
}