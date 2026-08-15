import 'package:dio/dio.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';

class PreferencesService {
  static final Dio _dio = Dio(BaseOptions(
    baseUrl: dotenv.env['API_BASE_URL'] ?? 'http://localhost:8000',
    connectTimeout: const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 30),
  ));

  static Future<Map<String, dynamic>> setPreference(String userId, String key, dynamic value) async {
    final response = await _dio.post('/api/v1/preferences/set', data: {'user_id': userId, 'key': key, 'value': value});
    return response.data;
  }

  static Future<Map<String, dynamic>> getPreferences(String userId) async {
    final response = await _dio.get('/api/v1/preferences/get/$userId');
    return response.data;
  }

  static Future<Map<String, dynamic>> registerDevice(String userId, String deviceId, {String? deviceName, String? deviceType, String? platform, Map<String, dynamic>? capabilities, Map<String, dynamic>? config}) async {
    final response = await _dio.post('/api/v1/devices/register', data: {
      'user_id': userId,
      'device_id': deviceId,
      'device_name': deviceName,
      'device_type': deviceType,
      'platform': platform,
      'capabilities': capabilities ?? {},
      'config': config ?? {},
    });
    return response.data;
  }

  static Future<Map<String, dynamic>> listDevices(String userId) async {
    final response = await _dio.get('/api/v1/devices/list/$userId');
    return response.data;
  }

  static Future<Map<String, dynamic>> heartbeat(String deviceId, {bool isOnline = true}) async {
    final response = await _dio.post('/api/v1/devices/$deviceId/heartbeat', data: {'is_online': isOnline});
    return response.data;
  }
}
