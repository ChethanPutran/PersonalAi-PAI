class DeviceInfo {
  final String id;
  final String name;
  final String deviceType;
  final String status;
  final String appVersion;
  final String runtimeVersion;
  final String platform;
  final String platformVersion;
  final String osVersion;
  final String architecture;
  final String hostname;
  final List<dynamic> capabilities;

  DeviceInfo({
    required this.id,
    required this.name,
    required this.deviceType,
    required this.status,
    required this.appVersion,
    required this.runtimeVersion,
    required this.platform,
    required this.platformVersion,
    required this.osVersion,
    required this.architecture,
    required this.hostname,
    required this.capabilities,
  });

  factory DeviceInfo.fromJson(Map<String, dynamic> json) {
    return DeviceInfo(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      deviceType: json['device_type']?.toString() ?? 'unknown',
      status: json['status']?.toString() ?? 'offline',
      appVersion: json['app_version']?.toString() ?? '',
      runtimeVersion: json['runtime_version']?.toString() ?? '',
      platform: json['platform']?.toString() ?? 'unknown',
      platformVersion: json['platform_version']?.toString() ?? '',
      osVersion: json['os_version']?.toString() ?? '',
      architecture: json['architecture']?.toString() ?? '',
      hostname: json['hostname']?.toString() ?? '',
      capabilities: json['capabilities'] is List
          ? List<dynamic>.from(json['capabilities'])
          : const [],
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'name': name,
      'device_type': deviceType,
      'status': status,
      'app_version': appVersion,
      'runtime_version': runtimeVersion,
      'platform': platform,
      'platform_version': platformVersion,
      'os_version': osVersion,
      'architecture': architecture,
      'hostname': hostname,
      'capabilities': capabilities,
    };
  }
}