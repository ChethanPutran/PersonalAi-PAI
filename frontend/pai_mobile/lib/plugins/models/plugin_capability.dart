class PluginCapability {
  final String id;
  final String description;
  final List<String> permissions;
  final Map<String, dynamic> parameters;

  const PluginCapability({
    required this.id,
    required this.description,
    this.permissions = const [],
    this.parameters = const {},
  });

  factory PluginCapability.fromJson(Map<String, dynamic> j) {
    return PluginCapability(
      id: j['id']?.toString() ?? '',
      description: j['description']?.toString() ?? '',
       permissions: (j['permissions'] as List?)?.map((e) => e.toString()).toList() ?? const [],
        parameters: j['parameters'] is Map
            ? Map<String, dynamic>.from(j['parameters'])
            : const {},
    );
  }
  /// The `properties` map from the parameters schema.
  Map<String, dynamic> get properties {
    final props = parameters['properties'];
    if (props is Map) return Map<String, dynamic>.from(props);
    return const {};
  }

  /// Defaults declared in the schema.
  Map<String, dynamic> get defaults {
    final out = <String, dynamic>{};
    properties.forEach((key, schema) {
      if (schema is Map && schema.containsKey('default')) {
        out[key] = schema['default'];
      }
    });
    return out;
  }


  Map<String, dynamic> toJson() => {
        'id': id,
        'description': description,
        'permissions': permissions,
        'parameters': parameters,
      };
}