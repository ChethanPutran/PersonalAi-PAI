import 'dart:io';

import 'package:shared_preferences/shared_preferences.dart';

import '../core/api/api_client.dart';
import 'device_info.dart';


class DeviceRegistration {
  final ApiClient api;

  DeviceRegistration(this.api);

  // -----------------------------------------------------------------
  // Payload
  // -----------------------------------------------------------------

  Future<Map<String, dynamic>> createRegistrationPayload() async {
    final prefs = await SharedPreferences.getInstance();
    final existingId = prefs.getString('device_id');

    final payload = <String, dynamic>{
      'device': {
        'name': 'PAI ${Platform.localHostname}',
        'device_type': _deviceType(),
        'app_version': '1.0.0',
        'runtime_version': '1.0.0',
        'platform': _platform(),
        'platform_version': Platform.operatingSystemVersion,
        'os_version': Platform.operatingSystemVersion,
        'architecture': _architecture(),
        'hostname': Platform.localHostname,
        'capabilities': [],
        'metadata': {},
      },
    };

    // Send the existing device_id so the backend can update that row
    // instead of minting a new one on every launch.
    if (existingId != null && existingId.isNotEmpty) {
      payload['device_id'] = existingId;
    }

    return payload;
  }

  // -----------------------------------------------------------------
  // Register
  // -----------------------------------------------------------------

  Future<DeviceInfo> register() async {
    final payload = await createRegistrationPayload();

    final response = await api.post('/devices/register', payload);

    if (response == null) {
      throw Exception('Device registration failed: empty response');
    }

    final deviceId = response['device_id']?.toString();
    if (deviceId == null || deviceId.isEmpty) {
      throw Exception('Device registration failed: backend did not return device_id');
    }

    final deviceJson = response['device'];
    late final DeviceInfo device;

    if (deviceJson is Map) {
      final json = Map<String, dynamic>.from(deviceJson);
      json['id'] = deviceId;
      device = DeviceInfo.fromJson(json);
    } else {
      device = DeviceInfo(
        id: deviceId,
        name: 'PAI ${Platform.localHostname}',
        deviceType: _deviceType(),
        status: 'online',
        appVersion: '1.0.0',
        runtimeVersion: '1.0.0',
        platform: _platform(),
        platformVersion: Platform.operatingSystemVersion,
        osVersion: Platform.operatingSystemVersion,
        architecture: _architecture(),
        hostname: Platform.localHostname,
        capabilities: const [],
      );
    }

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('device_id', deviceId);

    return device;
  }

  // -----------------------------------------------------------------
  // Local storage helpers
  // -----------------------------------------------------------------

  Future<String?> getStoredDeviceId() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString('device_id');
  }

  Future<void> clearDeviceId() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('device_id');
  }

  // -----------------------------------------------------------------
  // Platform helpers
  // -----------------------------------------------------------------

  String _platform() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isLinux) return 'linux';
    if (Platform.isWindows) return 'windows';
    if (Platform.isMacOS) return 'macos';
    if (Platform.isIOS) return 'ios';
    return 'unknown';
  }

  String _deviceType() {
    if (Platform.isAndroid || Platform.isIOS) return 'mobile';
    if (Platform.isLinux || Platform.isWindows || Platform.isMacOS) return 'desktop';
    return 'unknown';
  }

  String _architecture() {
    if (Platform.isAndroid) return 'arm64-v8a';
    if (Platform.isLinux || Platform.isWindows || Platform.isMacOS) return 'x86_64';
    return 'unknown';
  }
}