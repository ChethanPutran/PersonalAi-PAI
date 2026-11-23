import 'plugin_info.dart';
import 'plugin_capability.dart';

class PluginManifest {
  final PluginInfo info;
  final List<PluginCapability> capabilityDefinitions;

  const PluginManifest({
    required this.info,
    required this.capabilityDefinitions,
  });

  factory PluginManifest.fromJson(Map<String, dynamic> json) {
    final rawCapabilities = json['capabilities'];

    if (rawCapabilities is! List) {
      throw const FormatException(
        'Manifest must contain a capabilities array',
      );
    }

    final capabilities = rawCapabilities.map((item) {
      if (item is! Map) {
        throw const FormatException(
          'Each capability must be a JSON object',
        );
      }

      return PluginCapability.fromJson(
        Map<String, dynamic>.from(item),
      );
    }).toList();

    final ids = capabilities.map((c) => c.id).toList();

    if (ids.any((id) => id.isEmpty)) {
      throw const FormatException(
        'Capability IDs cannot be empty',
      );
    }

    if (ids.toSet().length != ids.length) {
      throw const FormatException(
        'Manifest contains duplicate capability IDs',
      );
    }

    return PluginManifest(
      info: PluginInfo.fromJson(json),
      capabilityDefinitions: List.unmodifiable(capabilities),
    );
  }

  String get id => info.id;
  String get version => info.version;
}