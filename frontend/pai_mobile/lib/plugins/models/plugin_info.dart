import 'plugin_capability.dart';

enum PluginState {
  available,
  installing,
  installed,
  enabled,
  disabled,
  unavailable,
  error,
}

PluginState pluginStateFromString(String? v) {
  switch (v) {
    case 'available':
      return PluginState.available;
    case 'installing':
      return PluginState.installing;
    case 'installed':
      return PluginState.installed;
    case 'enabled':
      return PluginState.enabled;
    case 'disabled':
      return PluginState.disabled;
    case 'unavailable':
      return PluginState.unavailable;
    case 'error':
      return PluginState.error;
    default:
      return PluginState.available;
  }
}

String pluginStateToString(PluginState s) {
  switch (s) {
    case PluginState.available:
      return 'available';
    case PluginState.installing:
      return 'installing';
    case PluginState.installed:
      return 'installed';
    case PluginState.enabled:
      return 'enabled';
    case PluginState.disabled:
      return 'disabled';
    case PluginState.unavailable:
      return 'unavailable';
    case PluginState.error:
      return 'error';
  }
}

class PlatformSpec {
  final String artifact;
  final String entrypoint;

  const PlatformSpec({
    required this.artifact,
    this.entrypoint = 'pai_plugin_module_create',
  });

  factory PlatformSpec.fromJson(Map<String, dynamic> j) => PlatformSpec(
        artifact: j['artifact']?.toString() ?? '',
        entrypoint:
            j['entrypoint']?.toString() ?? 'pai_plugin_module_create',
      );

  Map<String, dynamic> toJson() => {
        'artifact': artifact,
        'entrypoint': entrypoint,
      };
}

class PluginInfo {
  final String id;
  final String name;
  final String version;
  final String description;
  final String? author;
  final String? icon;
  final int schemaVersion;

  /// Union of all capability permissions.
  final List<String> permissions;

  /// Detailed capability descriptors (from manifest).
  final List<PluginCapability> capabilityDetails;

  /// Flat capability ids (from catalog or derived).
  final List<String> capabilities;

  /// Native module name for MethodChannel dispatch, e.g. "pai.camera".
  final String nativeModule;

  /// Minimum host version required.
  final String minPaiVersion;

  /// Per-platform artifact paths inside the package.
  final Map<String, PlatformSpec> platforms;

  /// Catalog fields.
  final List<String> availablePlatforms;
  final List<String> architectures;

  /// NEW: device types this plugin is installable on.
  /// Mirrors `manifest.device_types` (e.g. ["server"], ["desktop","mobile"]).
  final List<String> deviceTypes;

  final String? packageUrl;
  final String? checksum;
  final int size;

  /// NEW: what version is actually installed (from device_plugins.version).
  /// Only set when the plugin is installed on the queried device.
  final String? installedVersion;

  /// Mutable UI state.
  PluginState state;
  String? error;

  PluginInfo({
    required this.id,
    required this.name,
    required this.version,
    this.description = '',
    this.author,
    this.icon,
    this.schemaVersion = 1,
    this.permissions = const [],
    this.capabilityDetails = const [],
    this.capabilities = const [],
    this.nativeModule = '',
    this.minPaiVersion = '0.0.0',
    this.platforms = const {},
    this.availablePlatforms = const [],
    this.architectures = const [],
    this.deviceTypes = const [],
    this.packageUrl,
    this.checksum,
    this.size = 0,
    this.installedVersion,
    this.state = PluginState.available,
    this.error,
  });

  bool get isAvailable => state == PluginState.available;
  bool get isInstalling => state == PluginState.installing;
  bool get isInstalled =>
      state == PluginState.installed ||
      state == PluginState.enabled ||
      state == PluginState.disabled;
  bool get isEnabled => state == PluginState.enabled;
  bool get isDisabled => state == PluginState.disabled;
  bool get hasError => state == PluginState.error;

  // -----------------------------------------------------------------
  // CATALOG (from backend /plugins/catalog)
  //
  // The backend emits per-device groups. Each plugin entry looks like:
  //
  //   {
  //     "id": "browser",
  //     "name": "Browser",
  //     "version": "1.0.1",
  //     "description": "...",
  //     "platforms": ["linux"],
  //     "architectures": ["x86_64"],
  //     "device_types": ["server"],
  //     "capabilities": [...],
  //     "package_url": "...",
  //     "checksum": "...",
  //     "size": 0,
  //     "state": "available" | "installed" | "disabled" | "enabled",
  //     "is_installed": true | false,
  //     "enabled_on_device": true | false,
  //     "installed_version": "1.0.1" | null,
  //   }
  // -----------------------------------------------------------------
  factory PluginInfo.fromCatalogJson(Map<String, dynamic> j) {
    final capsRaw = j['capabilities'];
    final capIds = <String>[];
    final capDetails = <PluginCapability>[];

    if (capsRaw is List) {
      for (final c in capsRaw) {
        if (c is String) {
          capIds.add(c);
        } else if (c is Map) {
          final m = Map<String, dynamic>.from(c);
          capIds.add(m['id']?.toString() ?? '');
          capDetails.add(PluginCapability.fromJson(m));
        }
      }
    }

    // Backend sends `state` explicitly. When it's absent (older backend),
    // fall back to inferring from is_installed / enabled_on_device so the
    // tile still renders correctly.
    PluginState state;
    final rawState = j['state']?.toString();
    if (rawState != null && rawState.isNotEmpty) {
      state = pluginStateFromString(rawState);
    } else if (j['is_installed'] == true) {
      state = j['enabled_on_device'] == true
          ? PluginState.enabled
          : PluginState.disabled;
    } else {
      state = PluginState.available;
    }

    return PluginInfo(
      id: j['id']?.toString() ?? '',
      name: j['name']?.toString() ?? '',
      version: j['version']?.toString() ?? '1.0.0',
      description: j['description']?.toString() ?? '',
      capabilities: capIds,
      capabilityDetails: capDetails,
      availablePlatforms:
          (j['platforms'] as List?)?.cast<String>() ?? const [],
      architectures:
          (j['architectures'] as List?)?.cast<String>() ?? const [],
      deviceTypes:
          (j['device_types'] as List?)?.cast<String>() ?? const [],
      packageUrl: j['package_url']?.toString(),
      checksum: j['checksum']?.toString(),
      size: (j['size'] as num?)?.toInt() ?? 0,
      installedVersion: j['installed_version']?.toString(),
      state: state,
      error: j['error']?.toString(),
    );
  }

  // -----------------------------------------------------------------
  // MANIFEST (from manifest.json)
  // -----------------------------------------------------------------
  factory PluginInfo.fromManifestJson(Map<String, dynamic> j) {
    final platformsRaw = j['platforms'];
    final platformMap = <String, PlatformSpec>{};
    if (platformsRaw is Map) {
      platformsRaw.forEach((k, v) {
        if (v is Map) {
          platformMap[k.toString()] =
              PlatformSpec.fromJson(Map<String, dynamic>.from(v));
        }
      });
    }

    final capsRaw = j['capabilities'];
    final capDetails = <PluginCapability>[];
    final capIds = <String>[];
    if (capsRaw is List) {
      for (final c in capsRaw) {
        if (c is Map) {
          final cap = PluginCapability.fromJson(Map<String, dynamic>.from(c));
          capDetails.add(cap);
          capIds.add(cap.id);
        } else if (c is String) {
          capIds.add(c);
        }
      }
    }

    final runtime = (j['runtime'] as Map?)?.cast<String, dynamic>() ?? {};

    return PluginInfo(
      id: j['id']?.toString() ?? '',
      name: j['name']?.toString() ?? '',
      version: j['version']?.toString() ?? '',
      description: j['description']?.toString() ?? '',
      author: j['author']?.toString(),
      icon: j['icon']?.toString(),
      schemaVersion: (j['schemaVersion'] as num?)?.toInt() ?? 1,
      permissions: (j['permissions'] as List?)?.cast<String>() ?? const [],
      capabilityDetails: capDetails,
      capabilities: capIds,
      nativeModule: runtime['nativeModule']?.toString() ?? '',
      minPaiVersion: runtime['minPaiVersion']?.toString() ?? '0.0.0',
      platforms: platformMap,
      deviceTypes:
          (j['device_types'] as List?)?.cast<String>() ?? const [],
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'version': version,
        'description': description,
        'author': author,
        'icon': icon,
        'schemaVersion': schemaVersion,
        'permissions': permissions,
        'capabilities': capabilityDetails.map((c) => c.toJson()).toList(),
        'runtime': {
          'nativeModule': nativeModule,
          'minPaiVersion': minPaiVersion,
        },
        'platforms': platforms.map((k, v) => MapEntry(k, v.toJson())),
        'device_types': deviceTypes,
      };

  Map<String, dynamic> toCatalogJson() => {
        'id': id,
        'name': name,
        'version': version,
        'capabilities': capabilities,
        'platforms': availablePlatforms,
        'architectures': architectures,
        'device_types': deviceTypes,
        'package_url': packageUrl,
        'checksum': checksum,
        'size': size,
        'state': pluginStateToString(state),
        'error': error,
      };

  @override
  String toString() =>
      'PluginInfo(id=$id, version=$version, state=$state, '
      'deviceTypes=$deviceTypes)';
}