import 'package:flutter/material.dart';
import 'package:pai_mobile/models/device.dart';
import 'package:pai_mobile/profile/profile_service.dart';

class DeviceRepository extends ChangeNotifier {
  final ProfileService _profileService;

  DeviceRepository({
    required ProfileService profileService,
  }) : _profileService = profileService;

  List<DeviceInfo> _devices = [];

  List<DeviceInfo> get devices => List.unmodifiable(_devices);

  bool _isLoading = false;
  bool get isLoading => _isLoading;

  Future<void> loadDevices({
  required String userId,
  String? currentDeviceId,
}) async {
  _isLoading = true;
  notifyListeners();

  try {
    final fetched = await _profileService.getDevices(
      userId: userId,
      currentDeviceId: currentDeviceId,
    );

    // Apply a client-side overlay: the device we're running on is,
    // by definition, online (we have an active WS). This protects the
    // UI against a race where GET /devices/list hits the server a
    // fraction of a second before attach_connection writes ONLINE.
    _devices = fetched.map((d) {
      if (currentDeviceId != null && d.id == currentDeviceId) {
        return d.copyWith(connected: true, isCurrent: true);
      }
      return d;
    }).toList();
  } finally {
    _isLoading = false;
    notifyListeners();
  }
}
  DeviceInfo? getDevice(String deviceId) {
    for (final device in _devices) {
      if (device.id == deviceId) return device;
    }
    return null;
  }

  void clear() {
    _devices = [];
    notifyListeners();
  }
}