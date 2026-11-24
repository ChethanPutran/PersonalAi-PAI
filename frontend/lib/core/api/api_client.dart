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
    final uri = _uri(path, query);
    return _run('GET', uri, () => http.get(uri, headers: _headers));
  }

  Future<dynamic> post(
    String path,
    Map<String, dynamic> body, {
    Map<String, String>? headers,
  }) async {
    final uri = _uri(path);
    return _run(
      'POST',
      uri,
      () => http.post(
        uri,
        headers: {..._headers, ...?headers},
        body: jsonEncode(body),
      ),
    );
  }

  /// Full URL for [path] (after the `/api/v1` prefix) — for callers that stream
  /// (SSE) and so can't go through [get]/[post].
  Uri urlFor(String path) => _uri(path);

  /// Auth + content headers, for streaming callers.
  Map<String, String> get authHeaders => Map.unmodifiable(_headers);

  Future<dynamic> put(
    String path,
    Map<String, dynamic> body,
  ) async {
    final uri = _uri(path);
    return _run(
      'PUT',
      uri,
      () => http.put(uri, headers: _headers, body: jsonEncode(body)),
    );
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
    final uri = _uri(path);
    return _run(
      'PATCH',
      uri,
      () => http.patch(uri, headers: _headers, body: jsonEncode(body)),
    );
  }

  Future<dynamic> delete(String path) async {
    final uri = _uri(path);
    return _run('DELETE', uri, () => http.delete(uri, headers: _headers));
  }

  // -----------------------------------------------------------------
  // Request execution + debug logging
  // -----------------------------------------------------------------

  /// Sends one request and logs it with `debugPrint`:
  ///   `[ApiClient] GET /tasks -> 200 (84 ms)`
  /// A failure logs the status, the response body and the stack trace, so a screen that
  /// shows "Failed to load tasks" always leaves the real reason in the console. The
  /// Authorization header and the token are never logged.
  Future<dynamic> _run(
    String method,
    Uri uri,
    Future<http.Response> Function() send,
  ) async {
    final where = '$method ${uri.path}${uri.hasQuery ? '?${uri.query}' : ''}';
    final sw = Stopwatch()..start();
    try {
      final response = await send();
      debugPrint(
        '[ApiClient] $where -> ${response.statusCode} (${sw.elapsedMilliseconds} ms)',
      );
      try {
        return _handle(response);
      } catch (e, st) {
        debugPrint('[ApiClient] $where FAILED: $e');
        debugPrint(
          '[ApiClient] response body: ${_clip(utf8.decode(response.bodyBytes, allowMalformed: true))}',
        );
        debugPrintStack(stackTrace: st, label: '[ApiClient] $where', maxFrames: 8);
        rethrow;
      }
    } catch (e, st) {
      // Transport-level failure (no response): offline, DNS, refused, timeout...
      if (e is! ApiException && e is! UnauthorizedException) {
        debugPrint(
          '[ApiClient] $where NETWORK ERROR after ${sw.elapsedMilliseconds} ms: '
          '${e.runtimeType}: $e',
        );
        debugPrintStack(stackTrace: st, label: '[ApiClient] $where', maxFrames: 8);
      }
      rethrow;
    }
  }

  static String _clip(String s, [int max = 1500]) =>
      s.length <= max ? s : '${s.substring(0, max)}… (${s.length} chars)';

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
