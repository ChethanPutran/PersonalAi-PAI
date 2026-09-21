import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../config/app_config.dart';

class AuthService {
  static const _tag = '[AuthService]';
  static const _tokenKey = 'auth_token';
  static const _userIdKey = 'auth_user_id';
  static const _emailKey = 'auth_email';

  final String baseUrl;

  AuthService({String? baseUrl}) : baseUrl = baseUrl ?? AppConfig.baseUrl;

  // -----------------------------------------------------------------
  // Session state
  // -----------------------------------------------------------------

  String? _token;
  String? _userId;
  String? _email;

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
  }

  Future<void> _persist(String token, String userId, String email) async {
    _token = token;
    _userId = userId;
    _email = email;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_tokenKey, token);
    await prefs.setString(_userIdKey, userId);
    await prefs.setString(_emailKey, email);
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
  }

  // -----------------------------------------------------------------
  // Register
  // -----------------------------------------------------------------

  Future<void> register(String email, String password) async {
    debugPrint('$_tag register email=$email');

    final r = await http.post(
      Uri.parse('$baseUrl/auth/register'),
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
  }

  // -----------------------------------------------------------------
  // Login
  // -----------------------------------------------------------------

  Future<void> login(String email, String password) async {
    debugPrint('$_tag login email=$email');

    final r = await http.post(
      Uri.parse('$baseUrl/auth/login'),
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