import '../core/api/api_client.dart';
import '../models/user_device.dart';
import '../models/user_profile.dart';

/// User-facing profile + device management.
///
/// Backend routes wired up here:
///   GET    /api/v1/auth/me                     -> current user profile
///   GET    /api/v1/devices/list/{user_id}      -> devices for a user
///
/// Admin-only device management (from pai/api/routes/system.py):
///   POST   /api/v1/system/devices/{id}/disconnect
///   DELETE /api/v1/system/devices/{id}
///
/// NOTE: there is currently no user-facing rename / disconnect / remove
/// endpoint. Non-admins therefore only get *read* access to their own
/// device list. See `routes/devices_extra.py` (at the bottom of this
/// response) for drop-in additions.
class ProfileService {
  // Base path for all profile-related endpoints. The ApiClient will prepend the
  // configured API base URL and version prefix.
  static const String _profilePath = '/auth/me';
  static const String _devicesPath = '/devices/list';
  static const String _adminDevicesPath = '/system/devices';

  final ApiClient api;

  ProfileService({required this.api});

  Future<UserProfile> getProfile() async {
    final data = await api.get(_profilePath);
    if (data is Map<String, dynamic>) {
      return UserProfile.fromJson(data);
    }
    throw ApiException(500, 'Unexpected profile response: $data');
  }

  Future<List<UserDevice>> getDevices({
    required String userId,
    String? currentDeviceId,
  }) async {
    final data = await api.get('$_devicesPath/$userId');
    return _extractDeviceList(data)
        .map((j) => UserDevice.fromJson(j, currentDeviceId: currentDeviceId))
        .toList(growable: false);
  }

  /// POST /api/v1/devices/{id}/disconnect
  ///
  /// Detaches the live WebSocket but leaves the device registered, so the
  /// user can reconnect from that device whenever they want. Idempotent.
  Future<void> disconnectDevice(String deviceId) async {
    await api.post('$_devicesPath/$deviceId/disconnect', const {});
  }

  /// DELETE /api/v1/devices/{id}
  ///
  /// Permanently unregisters the device. On next launch the mobile app
  /// will re-register itself under a new id — so "Remove" is really
  /// "forget this installation".
  Future<void> removeDevice(String deviceId) async {
    await api.delete('$_devicesPath/$deviceId');
  }


  /// Admin-only.
  Future<void> adminDisconnectDevice(String deviceId) async {
    await api.post('$_adminDevicesPath/$deviceId/disconnect', const {});
  }

  /// Admin-only.
  Future<void> adminRemoveDevice(String deviceId) async {
    await api.delete('$_adminDevicesPath/$deviceId');
  }

  List<Map<String, dynamic>> _extractDeviceList(dynamic data) {
    // Matches {"devices": [...]} returned by list_devices()
    if (data is Map && data['devices'] is List) {
      return (data['devices'] as List)
          .whereType<Map>()
          .map((m) => m.cast<String, dynamic>())
          .toList();
    }
    if (data is List) {
      return data
          .whereType<Map>()
          .map((m) => m.cast<String, dynamic>())
          .toList();
    }
    return const [];
  }
}