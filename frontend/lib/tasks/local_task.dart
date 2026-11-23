enum LocalTaskStatus {
  pending,
  running,
  paused,
  completed,
  failed,
  cancelled,
}

class LocalTask {
  final String id;
  final String capability;
  final Map<String, dynamic> parameters;
  final DateTime createdAt;

  LocalTaskStatus status;
  Map<String, dynamic>? result;
  String? error;
  DateTime? startedAt;
  DateTime? endedAt;

  LocalTask({
    required this.id,
    required this.capability,
    required this.parameters,
    required this.createdAt,
    this.status = LocalTaskStatus.pending,
    this.result,
    this.error,
    this.startedAt,
    this.endedAt,
  });

  bool get isFinished =>
      status == LocalTaskStatus.completed ||
      status == LocalTaskStatus.failed ||
      status == LocalTaskStatus.cancelled;
}