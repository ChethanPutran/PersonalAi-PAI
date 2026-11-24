import 'schedule.dart';

/// One action the user may schedule, as listed by `GET /schedules/capabilities`.
class SchedulableCapability {
  final String id;
  final String description;
  final String pluginId;
  final List<String> deviceIds;

  /// JSON Schema of the action's parameters (object with `properties`, usually).
  final Map<String, dynamic> parametersSchema;
  final bool requiresConsent;
  final ScheduleRisk risk;
  final bool schedulable;
  final String? blockedReason;

  const SchedulableCapability({
    required this.id,
    this.description = '',
    this.pluginId = '',
    this.deviceIds = const [],
    this.parametersSchema = const {},
    this.requiresConsent = false,
    this.risk = ScheduleRisk.low,
    this.schedulable = true,
    this.blockedReason,
  });

  bool get isReminder => id == kReminderCapability;

  /// Group heading in the picker: the plugin, or the capability's namespace.
  String get group {
    if (pluginId.isNotEmpty) return pluginId;
    final i = id.indexOf('.');
    return i > 0 ? id.substring(0, i) : id;
  }

  /// Tolerant: missing fields default, unknown risk values read as `high`,
  /// and a non-map schema becomes an empty one.
  factory SchedulableCapability.fromJson(Map<String, dynamic> j) {
    final rawId = j['id'];
    if (rawId == null || rawId.toString().trim().isEmpty) {
      throw const FormatException('capability has no id');
    }
    final schema = j['parameters_schema'];
    return SchedulableCapability(
      id: rawId.toString(),
      description: j['description']?.toString() ?? '',
      pluginId: j['plugin_id']?.toString() ?? '',
      deviceIds: (j['device_ids'] is List)
          ? (j['device_ids'] as List).map((e) => e.toString()).toList()
          : const [],
      parametersSchema: schema is Map ? schema.cast<String, dynamic>() : const {},
      requiresConsent: j['requires_consent'] == true,
      risk: ScheduleRisk.fromWire(j['risk']),
      schedulable: j['schedulable'] != false,
      blockedReason: j['blocked_reason']?.toString(),
    );
  }

  /// Parses the `{"capabilities": [...]}` body, skipping malformed entries
  /// (reported through [onSkip]) instead of failing the whole list.
  static List<SchedulableCapability> listFromJson(
    Object? body, {
    void Function(Object? item, Object error)? onSkip,
  }) {
    final raw = body is Map ? body['capabilities'] : body;
    if (raw is! List) return const [];
    final out = <SchedulableCapability>[];
    for (final item in raw) {
      try {
        if (item is! Map) throw const FormatException('capability is not an object');
        out.add(SchedulableCapability.fromJson(item.cast<String, dynamic>()));
      } catch (e) {
        onSkip?.call(item, e);
      }
    }
    return out;
  }
}
