import 'dart:io';

class AppConfig {
  /// Path prefix for every versioned API endpoint.
  /// `ApiClient` prepends this to every caller-supplied path, so
  /// callers pass `'/tasks'`, not `'/api/v1/tasks'`.
  static const String apiPrefix = '/api/v1';


  /// Host-only base URL. No trailing slash, no path segment.
  static String get baseUrl {
    const fromEnv = String.fromEnvironment('API_BASE_URL');
    if (fromEnv.isNotEmpty) return fromEnv;
    if (Platform.isAndroid) return 'http://10.0.2.2:8000';
    return 'http://127.0.0.1:8000';
  }

   /// Plugin registry lives under the versioned API. Must end with `/`.
  static Uri get pluginRegistryUrl {
    const fromEnv = String.fromEnvironment('PLUGIN_REGISTRY_URL');
    if (fromEnv.isNotEmpty) {
      final s = fromEnv.endsWith('/') ? fromEnv : '$fromEnv/';
      return Uri.parse(s);
    }
    return Uri.parse('$baseUrl$apiPrefix/plugins/');
  }
}
