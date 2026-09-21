enum TaskStatus {
  queued,
  running,
  paused,
  completed,
  failed,
  cancelled,
}


class Task {
  final String id;
  final String title;
  TaskStatus status;
  final String? deviceId;
  final String? plugin;


  Task({
    required this.id,
    required this.title,
    required this.status,
    this.deviceId,
    this.plugin,
  });


  factory Task.fromJson(
    Map<String, dynamic> json,
  ) {
    return Task(
      id: json['id'],
      title: json['title'],
      status: TaskStatus.values.firstWhere(
        (value) => value.name == json['status'],
      ),
      deviceId: json['device_id'],
      plugin: json['plugin'],
    );
  }
}