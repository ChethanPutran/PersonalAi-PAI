import 'package:flutter/foundation.dart';

enum PluginLanguage { native, kotlin, swift, python, javascript, unknown }

enum RuntimeKind { server, device, unknown }

PluginLanguage pluginLanguageFromString(String? value) {
  return PluginLanguage.values.firstWhere(
    (e) => e.name == value,
    orElse: () => PluginLanguage.unknown,
  );
}

RuntimeKind runtimeKindFromString(String? value) {
  return RuntimeKind.values.firstWhere(
    (e) => e.name == value,
    orElse: () => RuntimeKind.unknown,
  );
}

class PluginRuntimeSpec {
  final RuntimeKind kind;
  final PluginLanguage language;
  final String nativeModule;
  final String? minPaiVersion;

  const PluginRuntimeSpec({
    required this.kind,
    required this.language,
    required this.nativeModule,
    this.minPaiVersion,
  });

  bool get isServer => kind == RuntimeKind.server;
  bool get isDevice => kind == RuntimeKind.device;

  factory PluginRuntimeSpec.fromJson(Map<String, dynamic> json) {
    return PluginRuntimeSpec(
      kind: runtimeKindFromString(json['kind']?.toString()),
      language: pluginLanguageFromString(json['language']?.toString()),
      nativeModule:
          json['native_module']?.toString() ??
          json['nativeModule']?.toString() ??
          '',
      minPaiVersion: (json['min_pai_version'] ?? json['minPaiVersion'])
          ?.toString(),
    );
  }

Map<String, dynamic> toJson() => {
  'kind': kind.name,
  'nativeModule': nativeModule,
  'minPaiVersion': minPaiVersion,
};
}

class PlatformSpec {
  final String platform;

  /// Package-relative path to the platform artifact.
  final String artifact;

  /// Platform-specific entrypoint, if applicable.
  final String entrypoint;

  final Map<String, dynamic> extra;

  const PlatformSpec({
    required this.platform,
    required this.artifact,
    required this.entrypoint,
    this.extra = const {},
  });

  factory PlatformSpec.fromJson(Map<String, dynamic> json) {
    final platform = json['platform']?.toString();
    final artifact = json['artifact']?.toString();
    final entrypoint = json['entrypoint']?.toString();

    if (platform == null || platform.isEmpty) {
      throw const FormatException(
        'PlatformSpec requires a platform identifier.',
      );
    }

    if (artifact == null || artifact.isEmpty) {
      throw const FormatException('PlatformSpec requires an artifact path.');
    }

    return PlatformSpec(
      platform: platform,
      artifact: artifact,
      entrypoint: entrypoint ?? '',
      extra: json['extra'] is Map
          ? Map<String, dynamic>.from(json['extra'] as Map)
          : const {},
    );
  }

  Map<String, dynamic> toJson() => {
    'platform': platform,
    'artifact': artifact,
    'entrypoint': entrypoint,
    'extra': extra,
  };
}

class PluginInfo {
  // Identity
  final String id;
  final String name;
  final String version;
  final String description;

  // Classification
  final PluginLanguage type;
  final RuntimeKind runtimeKind;

  // Targeting
  final List<String> architectures;
  final List<String> deviceTypes;
  final List<String> permissions;

  // Platform-specific build information
  final List<PlatformSpec> platformSpecs;

  // Runtime configuration
  final PluginRuntimeSpec runtimeSpec;

  // Capability IDs only; definitions are loaded from the manifest.
  final List<String> capabilities;
  final bool requiresConfirmation;

  // Packaging
  final String? packageUrl;
  final String? checksum;
  final int size;

  const PluginInfo({
    required this.id,
    required this.name,
    required this.version,
    this.description = '',
    required this.type,
    required this.runtimeKind,
    this.architectures = const [],
    this.deviceTypes = const [],
    this.permissions = const [],
    this.platformSpecs = const [],
    required this.runtimeSpec,
    this.capabilities = const [],
    this.requiresConfirmation = false,
    this.packageUrl,
    this.checksum,
    this.size = 0,
  });

  bool get isServer => runtimeKind == RuntimeKind.server;
  bool get isDevice => runtimeKind == RuntimeKind.device;

  factory PluginInfo.fromJson(Map<String, dynamic> json) {
    final runtimeJson =
    json['runtimeSpec'] ??
    json['runtime_spec'] ??
    json['runtime'];

    final runtime = runtimeJson is Map
        ? PluginRuntimeSpec.fromJson(Map<String, dynamic>.from(runtimeJson))
        : const PluginRuntimeSpec(
            kind: RuntimeKind.unknown,
            language: PluginLanguage.unknown,
            nativeModule: '',
          );                                         

    final rawRuntimeKind = (json['runtimeKind'] ?? json['runtime_kind'])
        ?.toString();

    final parsedRuntimeKind = runtimeKindFromString(rawRuntimeKind);

    final rawType = json['type']?.toString();
    final parsedType = pluginLanguageFromString(rawType);


    final id = json['id']?.toString() ?? '';
    final name = json['name']?.toString() ?? '';
    final version = json['version']?.toString() ?? '';

    if (id.isEmpty || name.isEmpty || version.isEmpty) {
      throw const FormatException('PluginInfo requires id, name, and version.');
    }

    return PluginInfo(
      id: id,
      name: name,
      version: version,
      description: json['description']?.toString() ?? '',

      type: parsedType,

      // Prefer explicit runtimeKind, then runtimeSpec.kind.
      runtimeKind: parsedRuntimeKind == RuntimeKind.unknown
          ? runtime.kind
          : parsedRuntimeKind,

      platformSpecs: _platformSpecList(
        json['platformSpecs'] ?? json['platform_specs'] ?? json['platforms'],
      ),
      architectures: _stringList(json['architectures']),
      deviceTypes: _stringList(json['deviceTypes'] ?? json['device_types']),
      permissions: _stringList(json['permissions']),

      runtimeSpec: runtime,
      capabilities: _stringList(json['capabilities']),

      requiresConfirmation:
          json['requiresConfirmation'] == true ||
          json['requires_confirmation'] == true,

      packageUrl: (json['packageUrl'] ?? json['package_url'])?.toString(),
      checksum: json['checksum']?.toString(),
      size: (json['size'] as num?)?.toInt() ?? 0,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'version': version,
    'description': description,
    'type': type.name,
    'runtimeKind': runtimeKind.name,
    'architectures': architectures,
    'deviceTypes': deviceTypes,
    'permissions': permissions,
    'platformSpecs': platformSpecs.map((e) => e.toJson()).toList(),
    'runtimeSpec': runtimeSpec.toJson(),
    'capabilities': capabilities,
    'requiresConfirmation': requiresConfirmation,
    'packageUrl': packageUrl,
    'checksum': checksum,
    'size': size,
  };


  static List<String> _stringList(dynamic value) {
    if (value is! List) return const [];

    return value
        .where((item) => item != null)
        .map((item) => item.toString())
        .toList(growable: false);
  }

  static List<PlatformSpec> _platformSpecList(dynamic value) {
    if (value is List) {
      return value
          .whereType<Map>()
          .map((item) {
            return PlatformSpec.fromJson(Map<String, dynamic>.from(item));
          })
          .toList(growable: false);
    }

    if (value is Map) {
      return value.entries
          .map((entry) {
            final platform = entry.key.toString();
            final spec = Map<String, dynamic>.from(entry.value as Map);

            return PlatformSpec.fromJson({...spec, 'platform': platform});
          })
          .toList(growable: false);
    }

    return const [];
  }

  List<String> get supportedPlatforms =>
    platformSpecs.map((spec) => spec.platform).toList();

  @override
  String toString() {
    return 'PluginInfo('
        'id: $id, '
        'name: $name, '
        'version: $version, '
        'runtimeKind: ${runtimeKind.name}, '
        'type: ${type.name}'
        ')';
  }
}
