import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import '../../auth/auth_service.dart';
import '../../config/app_config.dart';


/// HTTP client for the PAI backend.
///
/// **Base URL and `/api/v1` prefix are handled here.** Callers pass
/// the path *after* `/api/v1` — so `api.get('/tasks')` hits
/// `<baseUrl>/api/v1/tasks`, and `api.post('/devices/register', ...)`
/// hits `<baseUrl>/api/v1/devices/register`.
///
/// - `baseUrl`   — host-only, no trailing slash, no path segment.
///                 e.g. `http://10.0.2.2:8000`
/// - `apiPrefix` — prepended to every call. Default `/api/v1`.
///                 Override only for non-standard deployments.
///
/// **Do NOT include `/api/v1` in caller paths** — doing so produces
/// a doubled prefix. A `debugPrint` warning fires if you try, so the
/// bug surfaces in one test cycle instead of as a silent 404.
class ApiClient {
  final String baseUrl;
  final String apiPrefix;
  final AuthService? auth;

  String? get authToken => auth?.token;

  ApiClient({
    String? baseUrl,
    String? apiPrefix,
    this.auth,
  })  : baseUrl = _normalize(baseUrl ?? AppConfig.baseUrl),
        apiPrefix = _normalizePrefix(apiPrefix ?? AppConfig.apiPrefix);

  // -----------------------------------------------------------------
  // URL construction
  // -----------------------------------------------------------------

  static String _normalize(String url) {
    var s = url.trim();
    while (s.endsWith('/')) s = s.substring(0, s.length - 1);
    return s;
  }
  Uri resolveUrl(String url) {
  final uri = Uri.parse(url);

  if (uri.hasScheme) {
    return uri;
  }

  return Uri.parse(baseUrl).resolveUri(uri);
}

  static String _normalizePrefix(String p) {
    var s = p.trim();
    if (s.isEmpty) return '';
    if (!s.startsWith('/')) s = '/$s';
    while (s.endsWith('/')) s = s.substring(0, s.length - 1);
    return s;
  }

  /// Build a full URI from a caller-supplied path.
  ///
  /// `'/tasks'` and `'tasks'` both yield `<baseUrl><apiPrefix>/tasks`.
  Uri _uri(String path, [Map<String, String>? query]) {
    var p = path;
    if (!p.startsWith('/')) p = '/$p';

    if (apiPrefix.isNotEmpty && p.startsWith('$apiPrefix/')) {
      debugPrint(
        '[ApiClient] WARNING: path "$path" already starts with '
        '"$apiPrefix". Callers must pass the path *after* the prefix '
        "(e.g. '/tasks', not '$apiPrefix/tasks').",
      );
    }

    final uri = Uri.parse('$baseUrl$apiPrefix$p');
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
      'Content-Type': 'application/json; charset=utf-8',
      'Accept': 'application/json; charset=utf-8',
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

  Future<http.Response> send(http.BaseRequest request) async {
  request.headers.addAll(_headers);

  final client = http.Client();

  try {
    final streamedResponse = await client.send(request);
    final bodyBytes = await streamedResponse.stream.toBytes();

    return http.Response.bytes(
      bodyBytes,
      streamedResponse.statusCode,
      headers: streamedResponse.headers,
      request: request,
    );
  } finally {
    client.close();
  }
}

  Future<dynamic> patch(
    String path,
    Map<String, dynamic> body,
  ) async {
    final response = await http.patch(
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
  // Never touch response.body — it decodes bodyBytes with the server's
  // charset (or Latin-1 if absent). Use bodyBytes directly and decode
  // as UTF-8, which is what FastAPI emits.
  dynamic data;
  if (response.bodyBytes.isNotEmpty) {
    try {
      final text = utf8.decode(response.bodyBytes, allowMalformed: true);
      data = jsonDecode(text);
    } on FormatException catch (e) {
      // Body wasn't valid JSON — very common for gateway errors
      // (nginx HTML, Cloudflare challenge pages). Preserve the raw text
      // in `data` so the caller can see what the server actually sent.
      data = {
        '_parse_error': e.message,
        '_raw': utf8.decode(response.bodyBytes, allowMalformed: true),
      };
    }
  }

  // Auth check runs first — but extract the server's `detail` message
  // so callers see "Token expired" instead of just "Unauthorized".
  if (response.statusCode == 401) {
    auth?.handleUnauthorized();
    final msg = data is Map ? data['detail']?.toString() : null;
    throw UnauthorizedException(msg);
  }

  if (response.statusCode >= 200 && response.statusCode < 300) {
    return data;
  }

  throw ApiException(response.statusCode, data);
}
}


class UnauthorizedException implements Exception {
  final String? message;
  UnauthorizedException([this.message]);

  @override
  String toString() => message ?? 'Unauthorized';
}


class ApiException implements Exception {
  final int statusCode;
  final dynamic body;

  ApiException(this.statusCode, this.body);

  @override
  String toString() => 'API error $statusCode: $body';
}
