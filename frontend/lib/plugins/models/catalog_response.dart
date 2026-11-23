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
  final String deviceId;
  final List<PluginInfo> plugins;

  const OtherDeviceGroup({required this.deviceId, required this.plugins});
}

/// Parsed response from `GET /api/v1/plugins/catalog`.
///
/// The backend returns:
///
///   {
///     "device_id":
/// [
///     {
///       "name": "...",
///       "platform": "linux",
///       "architecture": "x86_64",
///       "device_type": "desktop",
/// ...
///     },..
/// ],
///     "device_id": [
///       {
///         "name": "worker-01",
///         "platform": "linux",
///         "architecture": "x86_64",
///         "device_type": "server",..
///       },
/// {...}]
///   }
class CatalogResponse {
  final List<PluginInfo> thisDevicePlugins;
  final List<OtherDeviceGroup> otherDeviceGroups;

  const CatalogResponse({
    required this.thisDevicePlugins,
    required this.otherDeviceGroups,
  });

  factory CatalogResponse.fromJson(
    Map<String, dynamic> response, String deviceId) {
    final thisDevicePlugins = <PluginInfo>[];
    final otherDeviceGroups = <OtherDeviceGroup>[];

    response.forEach((key, value) {
      final plugins = (value as List? ?? [])
          .map(
            (e) =>
                PluginInfo.fromJson(Map<String, dynamic>.from(e as Map)),
          )
          .toList();

      if (key == deviceId) {
        thisDevicePlugins.addAll(plugins);
      } else {
        otherDeviceGroups.add(
          OtherDeviceGroup(deviceId: key, plugins: plugins),
        );
      }
    });
    return CatalogResponse(
      thisDevicePlugins: thisDevicePlugins,
      otherDeviceGroups: otherDeviceGroups,
    );
  }
}
