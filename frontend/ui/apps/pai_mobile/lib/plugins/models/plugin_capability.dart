class PluginCapability {
  final String id;
  final String description;
  final List<String> permissions;
  final Map<String, dynamic> parametersSchema;

  const PluginCapability({
    required this.id,
    required this.description,
    this.permissions = const [],
    this.parametersSchema = const {},
  });

  factory PluginCapability.fromJson(Map<String, dynamic> j) {
    return PluginCapability(
      id: j['id']?.toString() ?? '',
      description: j['description']?.toString() ?? '',
      permissions: (j['permissions'] as List?)?.cast<String>() ?? const [],
      parametersSchema:
          (j['parameters'] as Map?)?.cast<String, dynamic>() ??
              (j['parametersSchema'] as Map?)?.cast<String, dynamic>() ??
              const {},
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'description': description,
        'permissions': permissions,
        'parameters': parametersSchema,
      };
}