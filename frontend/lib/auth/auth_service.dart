import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../config/app_config.dart';
import '../core/api/api_client.dart';


/// Authentication service.
///
/// Uses an internal [ApiClient] for every `/auth/*` call, so URL
/// construction, host normalization, `/api/v1` prefixing, and
/// error handling are all shared with the rest of the app.
///
/// The internal client is **unauthenticated** — login and register
/// are pre-auth endpoints. The shared app-level `ApiClient` (created
/// with `auth: this`) is what other services use once a token exists.
/// Keeping them separate avoids the circular dependency that would
/// result from an authenticated client being used to obtain its own
/// token.
class AuthService extends ChangeNotifier {
  static const _tag = '[AuthService]';
  static const _tokenKey = 'auth_token';
  static const _userIdKey = 'auth_user_id';
  static const _emailKey = 'auth_email';

  /// Host-only base URL. Normalized so `/api/v1` never sneaks in here.
  late final String baseUrl;

  /// Unauthenticated client for `/auth/login` and `/auth/register`.
  late final ApiClient _api;

  AuthService({String? baseUrl}) {
    this.baseUrl = _normalizeHostOnly(baseUrl ?? AppConfig.baseUrl);
    _api = ApiClient(baseUrl: this.baseUrl);
  }

  static String _normalizeHostOnly(String url) {
    var s = url.trim();
    while (s.endsWith('/')) s = s.substring(0, s.length - 1);
    final match = RegExp(r'/api/v\d+$').firstMatch(s);
    if (match != null) {
      debugPrint(
        '$_tag WARNING: baseUrl contains "/api/v1" — stripping to host-only. '
        'Set API_BASE_URL to the host (e.g. http://10.0.2.2:8000).',
      );
      s = s.substring(0, match.start);
    }
    return s;
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
  // Session persistence
  // -----------------------------------------------------------------

  Future<void> restore() async {
    final prefs = await SharedPreferences.getInstance();
    _token = prefs.getString(_tokenKey);
    _userId = prefs.getString(_userIdKey);
    _email = prefs.getString(_emailKey);

    debugPrint('$_tag restored session: user=$_userId');
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

  /// Called by the shared `ApiClient` when a request returns 401.
  /// Clears the local session so the UI falls back to the login
  /// screen instead of showing "not authenticated" errors forever.
  void handleUnauthorized() {
    if (!isAuthenticated) return;
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

      final body = await _api.post('/auth/register', {
        'email': email,
        'password': password,
      }) as Map<String, dynamic>;

      await _persist(
        body['access_token'] as String,
        body['user_id'] as String,
        body['email'] as String,
      );
      debugPrint('$_tag register ok: user=$_userId');
    } on UnauthorizedException catch (e) {
      throw AuthException(e.message ?? 'Registration failed');
    } on ApiException catch (e) {
      // 409 duplicate email, 400 bad password, etc.
      throw AuthException(_detailFrom(e) ?? 'HTTP ${e.statusCode}');
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

      final body = await _api.post('/auth/login', {
        'email': email,
        'password': password,
      }) as Map<String, dynamic>;

      await _persist(
        body['access_token'] as String,
        body['user_id'] as String,
        body['email'] as String,
      );
      debugPrint('$_tag login ok: user=$_userId');
    } on UnauthorizedException catch (e) {
      // Backend returns 401 for both "user not found" and "wrong password".
      // The API's own message is already generic and safe to surface.
      throw AuthException(e.message ?? 'Invalid email or password');
    } on ApiException catch (e) {
      throw AuthException(_detailFrom(e) ?? 'HTTP ${e.statusCode}');
    } finally {
      _loginInFlight = false;
    }
  }

  // -----------------------------------------------------------------
  // Error helpers
  // -----------------------------------------------------------------

  String? _detailFrom(ApiException e) {
    final body = e.body;
    if (body is Map && body['detail'] != null) {
      return body['detail'].toString();
    }
    return null;
  }
}

class AuthException implements Exception {
  final String message;
  AuthException(this.message);
  @override
  String toString() => message;
}
