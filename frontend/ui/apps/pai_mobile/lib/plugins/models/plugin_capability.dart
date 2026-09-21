class PluginCapability {
  final String id;            // e.g. "camera.capture"
  final String description;
  final List<String> permissions;
  final Map<String, dynamic> parametersSchema;

  const PluginCapability({
    required this.id,
    required this.description,
    this.permissions = const [],
    this.parametersSchema = const {},
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'description': description,
        'permissions': permissions,
        'parametersSchema': parametersSchema,
      };
}