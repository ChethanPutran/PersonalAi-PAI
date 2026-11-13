import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../config/app_config.dart';


/// Authentication service.
///
/// Extends `ChangeNotifier` so any screen that needs to react to a
/// login / logout / token-expiry can `addListener` and re-run whatever
/// depends on the token. The immediate caller is `ChatScreen`, which
/// needs to (re)open its status WebSocket the moment the token
/// becomes available — without that, the WS connect is a no-op and
/// the chat screen shows no live status.
///
/// Endpoint convention: this class talks to `/api/v1/auth/*`. If your
/// deployment places the API under a different prefix, use the
/// `apiPrefix` constructor argument.
class AuthService extends ChangeNotifier {
  static const _tag = '[AuthService]';
  static const _tokenKey = 'auth_token';
  static const _userIdKey = 'auth_user_id';
  static const _emailKey = 'auth_email';

  final String baseUrl;
  final String apiPrefix;

  AuthService({
    String? baseUrl,
    this.apiPrefix = '/api/v1',
  }) : baseUrl = _normalize(baseUrl ?? AppConfig.baseUrl);

  static String _normalize(String url) {
    var s = url.trim();
    while (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    return s;
  }

  Uri _url(String path) {
    final p = path.startsWith('/') ? path : '/$path';
    return Uri.parse('$baseUrl$apiPrefix$p');
  }

  // -----------------------------------------------------------------
  // Session state
  // -----------------------------------------------------------------

  String? _token;
  String? _userId;
  String? _email;
  bool _loginInFlight = false;

  String? get token => _token;
  String? get userId => _userId;
  String? get email => _email;
  bool get isAuthenticated => _token != null && _userId != null;

  // -----------------------------------------------------------------
  // Restore from disk on app start
  // -----------------------------------------------------------------

  Future<void> restore() async {
    final prefs = await SharedPreferences.getInstance();
    _token = prefs.getString(_tokenKey);
    _userId = prefs.getString(_userIdKey);
    _email = prefs.getString(_emailKey);

    debugPrint('$_tag restored session: user=$_userId');

    // Notify listeners on restore completion. If a screen added a
    // listener in initState *before* restore completed, this is what
    // tells it the token is now available. Without this, the screen
    // has to guess when restore finishes — and usually guesses wrong.
    notifyListeners();
  }

  Future<void> _persist(String token, String userId, String email) async {
    _token = token;
    _userId = userId;
    _email = email;

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_tokenKey, token);
    await prefs.setString(_userIdKey, userId);
    await prefs.setString(_emailKey, email);

    // Auth state changed: from logged-out to logged-in, or from one
    // user to another. Every listener (chat screen, plugin screen,
    // anything holding a WS) should re-establish its connection.
    notifyListeners();
  }

  Future<void> logout() async {
    _token = null;
    _userId = null;
    _email = null;

    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_tokenKey);
    await prefs.remove(_userIdKey);
    await prefs.remove(_emailKey);

    debugPrint('$_tag logged out');
    notifyListeners();
  }

  /// Called by `ApiClient` when a request returns 401. Clears the
  /// local session so the UI falls back to the login screen instead
  /// of showing "not authenticated" errors on every subsequent call.
  ///
  /// Fire-and-forget — `logout()` awaits `SharedPreferences`, and the
  /// caller (an `ApiClient` exception handler) can't wait for that.
  void handleUnauthorized() {
    if (!isAuthenticated) return;    // already logged out
    debugPrint('$_tag 401 → clearing stale session');
    unawaited(logout());
  }

  // -----------------------------------------------------------------
  // Register
  // -----------------------------------------------------------------

  Future<void> register(String email, String password) async {
    if (_loginInFlight) {
      throw AuthException('Another login/register is in progress');
    }
    _loginInFlight = true;

    try {
      debugPrint('$_tag register email=$email');

      final r = await http.post(
        _url('/auth/register'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'email': email, 'password': password}),
      );

      if (r.statusCode != 200) {
        throw AuthException(_errorFrom(r));
      }

      final body = jsonDecode(r.body) as Map<String, dynamic>;
      await _persist(
        body['access_token'] as String,
        body['user_id'] as String,
        body['email'] as String,
      );
      debugPrint('$_tag register ok: user=$_userId');
    } finally {
      _loginInFlight = false;
    }
  }

  // -----------------------------------------------------------------
  // Login
  // -----------------------------------------------------------------

  Future<void> login(String email, String password) async {
    if (_loginInFlight) {
      throw AuthException('Another login/register is in progress');
    }
    _loginInFlight = true;

    try {
      debugPrint('$_tag login email=$email');

      final r = await http.post(
        _url('/auth/login'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'email': email, 'password': password}),
      );

      if (r.statusCode != 200) {
        throw AuthException(_errorFrom(r));
      }

      final body = jsonDecode(r.body) as Map<String, dynamic>;
      await _persist(
        body['access_token'] as String,
        body['user_id'] as String,
        body['email'] as String,
      );
      debugPrint('$_tag login ok: user=$_userId');
    } finally {
      _loginInFlight = false;
    }
  }

  // -----------------------------------------------------------------
  // Errors
  // -----------------------------------------------------------------

  String _errorFrom(http.Response r) {
    try {
      final body = jsonDecode(r.body);
      if (body is Map && body['detail'] != null) {
        return body['detail'].toString();
      }
    } catch (_) {}
    return 'HTTP ${r.statusCode}';
  }
}

class AuthException implements Exception {
  final String message;
  AuthException(this.message);
  @override
  String toString() => message;
}