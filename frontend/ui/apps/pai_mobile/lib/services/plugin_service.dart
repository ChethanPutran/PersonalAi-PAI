import 'package:dio/dio.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';

import '../models/plugin_info.dart';

class PluginService {
  static final Dio _dio = Dio(
    BaseOptions(
      baseUrl: _normalizeBaseUrl(
        dotenv.env['API_BASE_URL'] ?? 'http://localhost:8000',
      ),
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 30),
    ),
  );

  static String _normalizeBaseUrl(String value) {
    return value.endsWith('/') ? value.substring(0, value.length - 1) : value;
  }

  static Future<Map<String, dynamic>> getHealth() async {
    final response = await _dio.get('/health');
    return Map<String, dynamic>.from(response.data as Map);
  }

  static Future<Map<String, dynamic>> getSystemInfo() async {
    final response = await _dio.get('/');
    return Map<String, dynamic>.from(response.data as Map);
  }

  static Future<List<PluginInfo>> getPlugins() async {
    print('➡️ Fetching plugins from /api/v1/plugins');
    final userId = const String.fromEnvironment('USER_ID', defaultValue: 'test');
    final response = await _dio.get(
      '/api/v1/plugins',
      queryParameters: {'user_id': userId},
    );
    final payload = Map<String, dynamic>.from(response.data as Map);
    final plugins = payload['plugins'] as List<dynamic>? ?? const [];
    print('✅ Plugins response: ${plugins.length} plugins found');
    return plugins
        .map(
          (plugin) =>
              PluginInfo.fromJson(Map<String, dynamic>.from(plugin as Map)),
        )
        .toList();
  }

  static Future<Map<String, dynamic>> getPlugin(String pluginId) async {
    print('➡️ Fetching plugin from /api/v1/plugins/$pluginId');
    final userId = const String.fromEnvironment('USER_ID', defaultValue: 'test');
    final response = await _dio.get(
      '/api/v1/plugins/$pluginId',
      queryParameters: {'user_id': userId},
    );
    print('✅ Plugin response: ${response.data}');
    return Map<String, dynamic>.from(response.data as Map);
  }

  static Future<Map<String, dynamic>> setPluginConfig(
      String pluginId, Map<String, dynamic> config) async {
    // Backend may not support persisting config yet. Attempt POST and
    // return whatever the server responds with or throw.
    final response = await _dio.post('/api/v1/plugins/$pluginId/config',
        data: config);
    return Map<String, dynamic>.from(response.data as Map);
  }

  static Future<void> setPluginEnabled(String pluginId, bool enabled) async {
    final endpoint = enabled ? 'enable' : 'disable';
    await _dio.post('/api/v1/plugins/$pluginId/$endpoint');
  }
}
