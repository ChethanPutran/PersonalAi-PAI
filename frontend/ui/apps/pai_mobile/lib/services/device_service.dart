import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

class DeviceService {
  static const _deviceIdKey = 'device_id';
  static const _userIdKey = 'user_id';

  /// Returns an existing device id or creates and persists a new UUID v4.
  static Future<String> getOrCreateDeviceId() async {
    final prefs = await SharedPreferences.getInstance();
    var id = prefs.getString(_deviceIdKey);
    if (id == null || id.isEmpty) {
      id = const Uuid().v4();
      await prefs.setString(_deviceIdKey, id);
    }
    return id;
  }

  /// Optionally persist or retrieve a user id (used as fallback).
  static Future<String> getOrCreateUserId({String fallback = 'default'}) async {
    final prefs = await SharedPreferences.getInstance();
    var id = prefs.getString(_userIdKey);
    if (id == null || id.isEmpty) {
      id = fallback;
      await prefs.setString(_userIdKey, id);
    }
    return id;
  }

  static Future<void> setUserId(String userId) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_userIdKey, userId);
  }
}
