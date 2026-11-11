import 'plugin_info.dart';

/// A device descriptor from the catalog response.
///
/// Used both for the "this device" entry and every entry in
/// `other_devices`. `connected` reflects whether the backend currently
/// has a live WebSocket for that device (only present on `other_devices`
/// entries; default `true` for `this_device`).
class DeviceDescriptor {
  final String deviceId;
  final String? name;
  final String platform;
  final String architecture;
  final String deviceType;
  final bool connected;

  const DeviceDescriptor({
    required this.deviceId,
    required this.name,
    required this.platform,
    required this.architecture,
    required this.deviceType,
    this.connected = true,
  });

  factory DeviceDescriptor.fromJson(
    Map<String, dynamic> j, {
    bool connected = true,
  }) {
    return DeviceDescriptor(
      deviceId: j['device_id']?.toString() ?? '',
      name: j['name']?.toString(),
      platform: j['platform']?.toString() ?? '',
      architecture: j['architecture']?.toString() ?? '',
      deviceType: j['device_type']?.toString() ?? '',
      connected: connected,
    );
  }

  /// Best-effort human label. Prefers the name the user assigned the
  /// device; falls back to "<type> (<platform>/<arch>)".
  String get label {
    if (name != null && name!.isNotEmpty) return name!;
    if (deviceType.isNotEmpty && platform.isNotEmpty) {
      return '$deviceType ($platform/$architecture)';
    }
    return deviceId;
  }

  @override
  String toString() =>
      'DeviceDescriptor(id=$deviceId, type=$deviceType, '
      'platform=$platform/$architecture, connected=$connected)';
}

/// One group of plugins compatible with a single *other* device owned by
/// the same user.
class OtherDeviceGroup {
  final DeviceDescriptor device;
  final List<PluginInfo> plugins;

  const OtherDeviceGroup({required this.device, required this.plugins});
}

/// Parsed response from `GET /api/v1/plugins/catalog`.
///
/// The backend returns:
///
///   {
///     "this_device": {
///       "device_id": "...",
///       "name": "...",
///       "platform": "linux",
///       "architecture": "x86_64",
///       "device_type": "desktop",
///       "plugins": [ ...plugin entries... ]
///     },
///     "other_devices": [
///       {
///         "device_id": "...",
///         "name": "worker-01",
///         "platform": "linux",
///         "architecture": "x86_64",
///         "device_type": "server",
///         "connected": true,
///         "plugins": [ ...plugin entries... ]
///       },
///       ...
///     ]
///   }
class CatalogResponse {
  final DeviceDescriptor thisDevice;
  final List<PluginInfo> thisDevicePlugins;
  final List<OtherDeviceGroup> otherDeviceGroups;

  const CatalogResponse({
    required this.thisDevice,
    required this.thisDevicePlugins,
    required this.otherDeviceGroups,
  });

  factory CatalogResponse.fromJson(Map<String, dynamic> j) {
    final thisDevRaw =
        (j['this_device'] as Map?)?.cast<String, dynamic>() ?? {};
    final thisDev = DeviceDescriptor.fromJson(thisDevRaw);

    final thisPlugins = (thisDevRaw['plugins'] as List? ?? [])
        .map((e) => PluginInfo.fromCatalogJson(
              Map<String, dynamic>.from(e as Map),
            ))
        .toList();

    final others = (j['other_devices'] as List? ?? [])
        .map((e) {
          final m = Map<String, dynamic>.from(e as Map);
          final dev = DeviceDescriptor.fromJson(
            m,
            connected: m['connected'] == true,
          );
          final plugins = (m['plugins'] as List? ?? [])
              .map((p) => PluginInfo.fromCatalogJson(
                    Map<String, dynamic>.from(p as Map),
                  ))
              .toList();
          return OtherDeviceGroup(device: dev, plugins: plugins);
        })
        .toList();

    return CatalogResponse(
      thisDevice: thisDev,
      thisDevicePlugins: thisPlugins,
      otherDeviceGroups: others,
    );
  }
}