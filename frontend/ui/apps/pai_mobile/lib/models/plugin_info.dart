class PluginInfo {
  final String id;
  final String name;
  final String version;
  final String type;
  final String description;
  final bool isEnabled;

  const PluginInfo({
    required this.id,
    required this.name,
    required this.version,
    required this.type,
    required this.description,
    required this.isEnabled,
  });

  factory PluginInfo.fromJson(Map<String, dynamic> json) {
  return PluginInfo(
    id: json['id']?.toString() ?? '',
    name: json['name']?.toString() ?? 'Unknown plugin',
    version: json['version']?.toString() ?? 'Unknown',
    type: json['type']?.toString() ?? 'unknown',
    description: json['description']?.toString() ?? '',
    isEnabled: json['is_enabled'] == true,
  );
}
}
