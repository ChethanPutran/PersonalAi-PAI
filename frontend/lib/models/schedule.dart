import '../ui/theme/pai_clock.dart';

/// Capability id of the classic notification reminder (the "Reminder" action).
const String kReminderCapability = 'system.notifications.show';

/// How risky an action is (drives the chip colour and how loudly consent is asked for).
/// Unknown wire values are treated as [high]: a risk we do not understand is shown cautiously.
enum ScheduleRisk {
  low,
  medium,
  high,
  critical;

  static ScheduleRisk fromWire(Object? wire) {
    if (wire == null) return ScheduleRisk.low;
    final w = wire.toString().toLowerCase();
    for (final v in ScheduleRisk.values) {
      if (v.name == w) return v;
    }
    return ScheduleRisk.high;
  }

  String get label => name;
}

/// Outcome of the most recent firing, as reported by the backend.
enum LastRunStatus {
  ok,
  skippedNoConsent,
  denied,
  failed,
  deviceOffline,
  unknown; // a status this client does not know about

  static LastRunStatus? fromWire(Object? wire) {
    if (wire == null) return null;
    return switch (wire.toString()) {
      'ok' => LastRunStatus.ok,
      'skipped_no_consent' => LastRunStatus.skippedNoConsent,
      'denied' => LastRunStatus.denied,
      'failed' => LastRunStatus.failed,
      'device_offline' => LastRunStatus.deviceOffline,
      _ => LastRunStatus.unknown,
    };
  }

  String get label => switch (this) {
        LastRunStatus.ok => 'Ran OK',
        LastRunStatus.skippedNoConsent => 'Skipped · needs approval',
        LastRunStatus.denied => 'Denied',
        LastRunStatus.failed => 'Failed',
        LastRunStatus.deviceOffline => 'Device offline',
        LastRunStatus.unknown => 'Unknown',
      };
}

/// Standing approval for a scheduled action to run unattended.
class ScheduleConsent {
  final bool active;
  final DateTime? grantedAt;
  final DateTime? expiresAt;

  const ScheduleConsent({this.active = false, this.grantedAt, this.expiresAt});

  static const none = ScheduleConsent();

  factory ScheduleConsent.fromJson(Object? j) {
    if (j is! Map) return ScheduleConsent.none;
    return ScheduleConsent(
      active: j['active'] == true,
      grantedAt: Schedule.parseTimestamp(j['granted_at']),
      expiresAt: Schedule.parseTimestamp(j['expires_at']),
    );
  }
}

/// What the UI shows for the consent state of a schedule.
enum ConsentStatus {
  notRequired,
  needsApproval,
  approved,
  expiringSoon, // approved, but < [Schedule.consentExpiringSoon] left
  expired;

  bool get blocksRunning => this == needsApproval || this == expired;
}

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

  // --- plugin-action support (all optional: absent on reminders / older backends) ---
  final bool requiresConsent;
  final ScheduleConsent consent;
  final DateTime? lastRunAt;
  final LastRunStatus? lastRunStatus;
  final String? lastRunError;
  final ScheduleRisk risk;
  final String? executor;

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
    this.requiresConsent = false,
    this.consent = ScheduleConsent.none,
    this.lastRunAt,
    this.lastRunStatus,
    this.lastRunError,
    this.risk = ScheduleRisk.low,
    this.executor,
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
        requiresConsent: j['requires_consent'] == true,
        consent: ScheduleConsent.fromJson(j['consent']),
        lastRunAt: _parse(j['last_run_at']),
        lastRunStatus: LastRunStatus.fromWire(j['last_run_status']),
        lastRunError: j['last_run_error']?.toString(),
        risk: ScheduleRisk.fromWire(j['risk']),
        executor: j['executor']?.toString(),
      );

  /// True for the classic notification reminder; false for a plugin action.
  bool get isReminder => capability == kReminderCapability;

  /// Approval is shown as "expiring soon" when fewer than this many days are left.
  static const Duration consentExpiringSoon = Duration(days: 3);

  /// The consent state to display at [now]. Approval only counts while it is unexpired:
  /// an `expires_at` in the past is "expired" even if the server's `active` flag lags.
  ConsentStatus consentStatus(DateTime now) {
    if (!requiresConsent) return ConsentStatus.notRequired;
    final exp = consent.expiresAt;
    if (exp != null && !exp.isAfter(now)) return ConsentStatus.expired;
    if (!consent.active) return ConsentStatus.needsApproval;
    if (exp != null && exp.difference(now) < consentExpiringSoon) {
      return ConsentStatus.expiringSoon;
    }
    return ConsentStatus.approved;
  }

  /// Public alias of the tolerant timestamp parser (naive ISO = UTC).
  static DateTime? parseTimestamp(dynamic v) => _parse(v);

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

  /// "Today 09:00" / "Nov 24 09:00" — the format used across the reminders UI.
  static String formatDateTime(DateTime d) => _fmtDateTime(d);

  static String _fmtDateTime(DateTime d) {
    final now = PaiClock.now(); // the app's clock hook (tests pin it), not the raw system clock
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