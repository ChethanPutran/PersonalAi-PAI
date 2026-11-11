class UserDevice {
  final String id;
  final String name;
  final String? platform;
  final String? architecture;
  final String? deviceType;
  final String? hostname;
  final String? appVersion;
  final String status;
  final bool connected;
  final DateTime? lastSeen;
  final bool isCurrent;

  const UserDevice({
    required this.id,
    required this.name,
    this.platform,
    this.architecture,
    this.deviceType,
    this.hostname,
    this.appVersion,
    this.status = 'unknown',
    this.connected = false,
    this.lastSeen,
    this.isCurrent = false,
  });

  factory UserDevice.fromJson(
    Map<String, dynamic> json, {
    String? currentDeviceId,
  }) {
    final id = json['id']?.toString() ?? '';
    final lastSeen = json['last_seen']?.toString();
    return UserDevice(
      id: id,
      name: json['name']?.toString() ?? 'Unknown device',
      platform: json['platform']?.toString(),
      architecture: json['architecture']?.toString(),
      deviceType: json['device_type']?.toString(),
      hostname: json['hostname']?.toString(),
      appVersion: json['app_version']?.toString(),
      status: json['status']?.toString() ?? 'unknown',
      connected: json['connected'] == true,
      lastSeen: lastSeen != null ? DateTime.tryParse(lastSeen) : null,
      isCurrent: currentDeviceId != null && id == currentDeviceId,
    );
  }

  String get subtitleParts {
    final parts = <String>[];
    if (platform != null && platform!.isNotEmpty) {
      parts.add(architecture != null && architecture!.isNotEmpty
          ? '$platform ($architecture)'
          : platform!);
    }
    if (deviceType != null && deviceType!.isNotEmpty) {
      parts.add(deviceType!);
    }
    return parts.join(' · ');
  }

  String get lastSeenLabel {
    if (isCurrent) return 'This device';
    if (connected) return 'Online now';
    if (lastSeen == null) return 'Never seen';
    final diff = DateTime.now().difference(lastSeen!.toLocal());
    if (diff.inSeconds < 60) return 'Just now';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    if (diff.inDays < 7) return '${diff.inDays}d ago';
    return lastSeen!.toLocal().toString().split(' ').first;
  }
}