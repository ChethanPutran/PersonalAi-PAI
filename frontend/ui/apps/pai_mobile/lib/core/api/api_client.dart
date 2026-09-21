import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../auth/auth_service.dart';
import '../../config/app_config.dart';


class ApiClient {
  /// Base URL of the backend.
  ///
  /// Must NOT end with a trailing slash or a path segment.
  /// Example: "http://127.0.0.1:8000" or "http://10.0.2.2:8000".
  ///
  /// Callers pass paths like "/api/v1/plugins/catalog" — the "/api/v1"
  /// prefix belongs to the caller, not to this class, because the
  /// backend may serve non-versioned paths too.
  final String baseUrl;

  final AuthService? auth;

  ApiClient({
    String? baseUrl,
    this.auth,
  }) : baseUrl = _normalize(baseUrl ?? AppConfig.baseUrl);

  // -----------------------------------------------------------------
  // URL construction
  // -----------------------------------------------------------------

  static String _normalize(String url) {
    var s = url.trim();
    while (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    return s;
  }

  /// Build a URI from this client's base URL and a caller-supplied path.
  ///
  /// Guarantees exactly one slash between the base and the path,
  /// regardless of whether the caller wrote `/foo` or `foo`.
  Uri _uri(String path, [Map<String, String>? query]) {
    var p = path;
    if (!p.startsWith('/')) p = '/$p';

    final uri = Uri.parse('$baseUrl$p');
    if (query == null || query.isEmpty) return uri;
    return uri.replace(queryParameters: {
      ...uri.queryParameters,
      ...query,
    });
  }

  // -----------------------------------------------------------------
  // Headers
  // -----------------------------------------------------------------

  Map<String, String> get _headers {
    final h = <String, String>{
      'Content-Type': 'application/json',
    };
    final token = auth?.token;
    if (token != null && token.isNotEmpty) {
      h['Authorization'] = 'Bearer $token';
    }
    return h;
  }

  // -----------------------------------------------------------------
  // Verbs
  // -----------------------------------------------------------------

  Future<dynamic> get(
    String path, {
    Map<String, String>? query,
  }) async {
    final response = await http.get(_uri(path, query), headers: _headers);
    return _handle(response);
  }

  Future<dynamic> post(
    String path,
    Map<String, dynamic> body,
  ) async {
    final response = await http.post(
      _uri(path),
      headers: _headers,
      body: jsonEncode(body),
    );
    return _handle(response);
  }

  Future<dynamic> put(
    String path,
    Map<String, dynamic> body,
  ) async {
    final response = await http.put(
      _uri(path),
      headers: _headers,
      body: jsonEncode(body),
    );
    return _handle(response);
  }

  Future<dynamic> delete(String path) async {
    final response = await http.delete(_uri(path), headers: _headers);
    return _handle(response);
  }

  // -----------------------------------------------------------------
  // Response handling
  // -----------------------------------------------------------------

  dynamic _handle(http.Response response) {
    final data = response.body.isEmpty
        ? null
        : jsonDecode(response.body);

    if (response.statusCode == 401) {
      throw UnauthorizedException();
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      return data;
    }

    throw ApiException(response.statusCode, data);
  }
}


class UnauthorizedException implements Exception {
  @override
  String toString() => 'Unauthorized';
}


class ApiException implements Exception {
  final int statusCode;
  final dynamic body;

  ApiException(this.statusCode, this.body);

  @override
  String toString() => 'API error $statusCode: $body';
}