import 'dart:io';

class AppConfig {
  static String get baseUrl {
    const fromEnv = String.fromEnvironment('API_BASE_URL');
    if (fromEnv.isNotEmpty) return fromEnv;
    if (Platform.isAndroid) return 'http://10.0.2.2:8000/api/v1';
    return 'http://127.0.0.1:8000/api/v1';
  }

  /// Registry base. MUST end with a trailing slash so that
  /// `resolve('plugins/index.json')` produces
  /// `<base>/plugins/index.json` and not `<parent>/plugins/index.json`.
  static Uri get pluginRegistryUrl {
    const fromEnv = String.fromEnvironment('PLUGIN_REGISTRY_URL');
    if (fromEnv.isNotEmpty) {
      final s = fromEnv.endsWith('/') ? fromEnv : '$fromEnv/';
      return Uri.parse(s);
    }
    return Uri.parse('$baseUrl/plugins/');
  }

}