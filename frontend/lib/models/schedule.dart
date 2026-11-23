class Schedule {
  final String id;
  final String userId;
  final String? targetDeviceId;
  final String title;
  final String? body;
  final String kind;              // one_shot | cron | interval
  final DateTime? fireAt;
  final String? cron;
  final int? intervalSeconds;
  final String timezone;
  final String capability;
  final Map<String, dynamic> parameters;
  final String catchUp;           // none | once | all
  final bool enabled;
  final DateTime? lastFiredAt;
  final DateTime? nextFireAt;
  final DateTime createdAt;
  final DateTime updatedAt;

  const Schedule({
    required this.id,
    required this.userId,
    this.targetDeviceId,
    required this.title,
    this.body,
    required this.kind,
    this.fireAt,
    this.cron,
    this.intervalSeconds,
    required this.timezone,
    required this.capability,
    required this.parameters,
    required this.catchUp,
    required this.enabled,
    this.lastFiredAt,
    this.nextFireAt,
    required this.createdAt,
    required this.updatedAt,
  });

  factory Schedule.fromJson(Map<String, dynamic> j) => Schedule(
        id: j['id'].toString(),
        userId: j['user_id'].toString(),
        targetDeviceId: j['target_device_id']?.toString(),
        title: j['title'].toString(),
        body: j['body']?.toString(),
        kind: j['kind'].toString(),
        fireAt: _parse(j['fire_at']),
        cron: j['cron']?.toString(),
        intervalSeconds: (j['interval_seconds'] as num?)?.toInt(),
        timezone: j['timezone']?.toString() ?? 'UTC',
        capability:
            j['capability']?.toString() ?? 'system.notifications.show',
        parameters: (j['parameters'] as Map?)?.cast<String, dynamic>() ?? {},
        catchUp: j['catch_up']?.toString() ?? 'once',
        enabled: j['enabled'] == true,
        lastFiredAt: _parse(j['last_fired_at']),
        nextFireAt: _parse(j['next_fire_at']),
        createdAt: _parse(j['created_at']) ?? DateTime.now(),
        updatedAt: _parse(j['updated_at']) ?? DateTime.now(),
      );

  static DateTime? _parse(dynamic v) {
  if (v == null) return null;
  final s = v.toString();

  // Detect whether the string carries a zone marker.
  final hasTz = s.endsWith('Z') ||
      RegExp(r'[+-]\d{2}:?\d{2}$').hasMatch(s);

  final parsed = DateTime.tryParse(s);
  if (parsed == null) return null;

  // Naive ISO → the backend means UTC. Dart would have treated it as
  // local, which is exactly what made 13:19 show as 06:49.
  final utc = hasTz ? parsed.toUtc() : DateTime.utc(
    parsed.year, parsed.month, parsed.day,
    parsed.hour, parsed.minute, parsed.second,
    parsed.millisecond, parsed.microsecond,
  );
  return utc.toLocal();
}

  /// Human-friendly repeat label.
  String get repeatLabel {
    switch (kind) {
      case 'one_shot':
        return fireAt != null ? 'Once · ${_fmtDateTime(fireAt!)}' : 'Once';
      case 'interval':
        final s = intervalSeconds ?? 0;
        if (s % 86400 == 0) return 'Every ${s ~/ 86400}d';
        if (s % 3600 == 0) return 'Every ${s ~/ 3600}h';
        if (s % 60 == 0) return 'Every ${s ~/ 60}m';
        return 'Every ${s}s';
      case 'cron':
        return 'Cron: ${cron ?? '—'}';
    }
    return kind;
  }

  String get nextLabel {
    if (!enabled) return 'Disabled';
    if (nextFireAt == null) return 'No upcoming run';
    return 'Next: ${_fmtDateTime(nextFireAt!)}';
  }

  static String _fmtDateTime(DateTime d) {
    final now = DateTime.now();
    final sameDay = d.year == now.year &&
        d.month == now.month &&
        d.day == now.day;
    final time = '${d.hour.toString().padLeft(2, '0')}:'
        '${d.minute.toString().padLeft(2, '0')}';
    if (sameDay) return 'Today $time';
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    return '${months[d.month - 1]} ${d.day} $time';
  }
}