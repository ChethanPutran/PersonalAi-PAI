
import 'package:pai_mobile/plugins/models/plugin_capability.dart';

class DeviceInfo {
  // Identity
  final String id;
  final String name;

  // Application information
  final String appVersion;
  final String runtimeVersion;

  // Platform information
  final String platform;
  final String? platformVersion;
  final String osVersion;
  final String architecture;

  // Device description
  final String? description;
  final String deviceType;
  final String status;

  // Network information
  final String? hostname;
  final String? address;
  final int? port;

  // Capabilities & metadata
  final List<PluginCapability> capabilities;
  final Map<String, dynamic> metadata;

  // Execution
  final String? executorId;

  // Timestamps
  final DateTime? registeredAt;
  final DateTime? lastSeen;

  // Client-side state (not part of backend DeviceInfo)
  final bool connected;
  final bool isCurrent;

  const DeviceInfo({
    required this.id,
    required this.name,
    required this.appVersion,
    required this.runtimeVersion,
    required this.platform,
    this.platformVersion,
    required this.osVersion,
    required this.architecture,
    this.description,
    required this.deviceType,
    required this.status,
    this.hostname,
    this.address,
    this.port,
    this.capabilities = const [],
    this.metadata = const {},
    this.executorId,
    this.registeredAt,
    this.lastSeen,
    this.connected = false,
    this.isCurrent = false,
  });

  factory DeviceInfo.fromJson(
  Map<String, dynamic> json, {
  String? currentDeviceId,
}) {
  final id = json['id']?.toString() ?? '';
  final status = json['status']?.toString().toLowerCase() ?? 'unknown';

  // The backend serialises `status` as "online"/"offline"/etc. There is
  // no top-level `connected` boolean on the wire — that was a client
  // model assumption. Derive it from `status` so the rest of the UI
  // (lastSeenLabel, connectivity dot, etc.) is consistent with the
  // server. If the backend ever adds a real `connected` field, prefer
  // it — but fall back to status.
  final connected = json['connected'] is bool
      ? json['connected'] as bool
      : status == 'online';

  return DeviceInfo(
    id: id,
    name: json['name']?.toString() ?? 'Unknown device',
    appVersion: json['app_version']?.toString() ?? '',
    runtimeVersion: json['runtime_version']?.toString() ?? '',
    platform: json['platform']?.toString() ?? 'unknown',
    platformVersion: json['platform_version']?.toString(),
    osVersion: json['os_version']?.toString() ?? '',
    architecture: json['architecture']?.toString() ?? 'unknown',
    description: json['description']?.toString(),
    deviceType: json['device_type']?.toString() ?? 'unknown',
    status: status,
    hostname: json['hostname']?.toString(),
    address: json['address']?.toString(),
    port: (json['port'] as num?)?.toInt(),
    capabilities: (json['capabilities'] as List? ?? [])
        .map((e) => PluginCapability.fromJson(
              Map<String, dynamic>.from(e as Map),
            ))
        .toList(),
    metadata: Map<String, dynamic>.from(json['metadata'] ?? {}),
    executorId: json['executor_id']?.toString(),
    registeredAt: _parseDateTime(json['registered_at']),
    lastSeen: _parseDateTime(json['last_seen']),
    connected: connected,                       // ← derived
    isCurrent: currentDeviceId != null && id == currentDeviceId,
  );
}

DeviceInfo copyWith({
  String? status,
  bool? connected,
  bool? isCurrent,
  DateTime? lastSeen,
}) {
  return DeviceInfo(
    id: id,
    name: name,
    appVersion: appVersion,
    runtimeVersion: runtimeVersion,
    platform: platform,
    platformVersion: platformVersion,
    osVersion: osVersion,
    architecture: architecture,
    description: description,
    deviceType: deviceType,
    status: status ?? this.status,
    hostname: hostname,
    address: address,
    port: port,
    capabilities: capabilities,
    metadata: metadata,
    executorId: executorId,
    registeredAt: registeredAt,
    lastSeen: lastSeen ?? this.lastSeen,
    connected: connected ?? this.connected,
    isCurrent: isCurrent ?? this.isCurrent,
  );
}

  static DateTime? _parseDateTime(dynamic value) {
    if (value == null) return null;
    return DateTime.tryParse(value.toString());
  }

  String get subtitleParts {
    final parts = <String>[];

    if (platform.isNotEmpty) {
      parts.add(
        architecture.isNotEmpty && architecture != 'unknown'
            ? '$platform ($architecture)'
            : platform,
      );
    }

    if (deviceType.isNotEmpty && deviceType != 'unknown') {
      parts.add(deviceType);
    }

    return parts.join(' · ');
  }

 String get lastSeenLabel {
  if (isCurrent) return 'This device';
  if (connected || status == 'online') return 'Online now';
  if (lastSeen == null) return 'Never seen';

  final localLastSeen = lastSeen!.toLocal();
  final diff = DateTime.now().difference(localLastSeen);
  if (diff.inSeconds < 60) return 'Just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff.inDays < 7) return '${diff.inDays}d ago';
  return localLastSeen.toString().split(' ').first;
}
}