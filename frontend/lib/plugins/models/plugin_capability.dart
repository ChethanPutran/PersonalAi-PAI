import 'package:flutter/foundation.dart';

@immutable
class PluginCapability {
  final String id;
  final String description;
  final bool requireConfirmation;
  final String? confirmationReason;
  final String? risk;
  final List<String> permissions;
  final Map<String, dynamic> parameters;

  const PluginCapability({
    required this.id,
    this.description = '',
    this.requireConfirmation = false,
    this.confirmationReason,
    this.risk,
    this.permissions = const [],
    this.parameters = const {},
  });

/// JSON Schema properties defining this capability's parameters.
Map<String, dynamic> get properties {
  final rawProperties = parameters['properties'];

  if (rawProperties is! Map) {
    return const {};
  }

  return Map<String, dynamic>.from(rawProperties);
}

  factory PluginCapability.fromJson(Map<String, dynamic> json) {
    final rawPermissions = json['permissions'];
    final rawParameters = json['parameters'];

    return PluginCapability(
      id: json['id']?.toString() ?? '',
      description: json['description']?.toString() ?? '',
      requireConfirmation:
          json['requireConfirmation'] == true ||
          json['require_confirmation'] == true,
      confirmationReason:
          json['confirmationReason']?.toString() ??
          json['confirmation_reason']?.toString(),
      risk: json['risk']?.toString(),
      permissions: rawPermissions is List
          ? rawPermissions.map((e) => e.toString()).toList()
          : const [],
      parameters: rawParameters is Map
          ? Map<String, dynamic>.from(rawParameters)
          : const {},
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'description': description,
    'requireConfirmation': requireConfirmation,
    if (confirmationReason != null)
      'confirmationReason': confirmationReason,
    if (risk != null) 'risk': risk,
    'permissions': permissions,
    'parameters': parameters,
  };
}