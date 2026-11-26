import 'dart:io';

import 'package:shared_preferences/shared_preferences.dart';

class AppConfig {
  /// Path prefix for every versioned API endpoint.
  /// `ApiClient` prepends this to every caller-supplied path, so
  /// callers pass `'/tasks'`, not `'/api/v1/tasks'`.
  static const String apiPrefix = '/api/v1';

  static const String _serverKey = 'server.base_url';

  /// The server address the user chose in the app (Profile > PAI server). Null = use the built-in default.
  static String? _override;

  /// Host-only base URL. No trailing slash, no path segment.
  /// Order: the address saved in the app, then the build-time `API_BASE_URL`, then the platform default.
  static String get baseUrl => _override ?? defaultBaseUrl;

  /// What the app uses when the user has not chosen a server.
  static String get defaultBaseUrl {
    const fromEnv = String.fromEnvironment('API_BASE_URL');
    if (fromEnv.isNotEmpty) return fromEnv;
    if (Platform.isAndroid) return 'http://10.0.2.2:8000';
    return 'http://127.0.0.1:8000';
  }

  /// True when the user saved an address of their own.
  static bool get hasCustomServer => _override != null;

  /// Reads the saved address; call once before the app starts. Never throws.
  static Future<void> loadSavedServer() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final v = prefs.getString(_serverKey);
      _override = (v == null || v.trim().isEmpty) ? null : v.trim();
    } catch (_) {
      _override = null;
    }
  }

  /// Saves (or, with null, clears) the server address and makes [baseUrl] return it from now on.
  /// Clients already created keep the old address: the app rebuilds them (see PaiApp).
  static Future<void> saveServer(String? url) async {
    _override = (url == null || url.trim().isEmpty) ? null : url.trim();
    try {
      final prefs = await SharedPreferences.getInstance();
      if (_override == null) {
        await prefs.remove(_serverKey);
      } else {
        await prefs.setString(_serverKey, _override!);
      }
    } catch (_) {/* kept for this run; the next start falls back to the default */}
  }

   /// Plugin registry lives under the versioned API. Must end with `/`.
  static Uri get pluginRegistryUrl {
    return Uri.parse('$baseUrl$apiPrefix/plugins/');
  }
}
