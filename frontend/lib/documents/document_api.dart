import 'dart:async';
import 'dart:io';

import '../core/api/api_client.dart';
import 'index_diff.dart';

/// One file's text chunks, as the backend takes them.
class IndexFilePayload {
  final String path;
  final double mtime;
  final int size;
  final List<String> chunks;
  const IndexFilePayload(this.path, this.mtime, this.size, this.chunks);

  Map<String, dynamic> toJson() => {
        'path': path,
        'mtime': mtime,
        'size': size,
        'chunks': [for (final c in chunks) {'text': c}],
      };
}

/// What the backend says it holds for this device.
class IndexServerStatus {
  final int files;
  final int chunks;
  final bool? indexingEnabled;
  const IndexServerStatus({this.files = 0, this.chunks = 0, this.indexingEnabled});
}

/// The user switched "Index documents" off on the server side: stop, do not retry.
class IndexingOffException implements Exception {
  final String message;
  const IndexingOffException([this.message = "Turn on 'Index documents' in the Files plugin settings first."]);
  @override
  String toString() => message;
}

/// The backend refused the size of the request (413 `index_limit`).
class IndexLimitException implements Exception {
  final String message;
  const IndexLimitException(this.message);
  @override
  String toString() => message;
}

/// Worth retrying later: 429, 5xx or no connection.
class IndexTransientException implements Exception {
  final String message;
  const IndexTransientException(this.message);
  @override
  String toString() => message;
}

/// The four document endpoints; a fake implements it in tests.
abstract class DocumentIndexApi {
  Future<Map<String, FileMeta>> indexedFiles(String deviceId);
  Future<void> index(String deviceId, List<IndexFilePayload> files);
  Future<int> remove(String deviceId, {List<String> paths = const [], String? prefix});
  Future<IndexServerStatus> status(String deviceId);
}

class HttpDocumentIndexApi implements DocumentIndexApi {
  final ApiClient api;
  HttpDocumentIndexApi(this.api);

  Never _map(Object e) {
    if (e is ApiException) {
      final body = e.body;
      final detail = body is Map ? body['detail'] : null;
      final code = detail is Map ? detail['code']?.toString() : null;
      final msg = detail is Map ? detail['message']?.toString() : (detail is String ? detail : null);
      if (e.statusCode == 409 && code == 'indexing_off') {
        throw msg == null ? const IndexingOffException() : IndexingOffException(msg);
      }
      if (e.statusCode == 413) throw IndexLimitException(msg ?? 'The document index is full or the request was too big.');
      if (e.statusCode == 429 || e.statusCode >= 500) {
        throw IndexTransientException(msg ?? 'The server is busy (${e.statusCode}).');
      }
    }
    if (e is SocketException || e is TimeoutException || e is HttpException) {
      throw const IndexTransientException("Couldn't reach the server.");
    }
    throw e;
  }

  @override
  Future<Map<String, FileMeta>> indexedFiles(String deviceId) async {
    try {
      final r = await api.get('/documents/files', query: {'device_id': deviceId});
      final files = r is Map ? r['files'] : null;
      final out = <String, FileMeta>{};
      if (files is Map) {
        for (final e in files.entries) {
          final m = FileMeta.fromJson(e.value);
          if (m != null) out[e.key.toString()] = m;
        }
      }
      return out;
    } catch (e) {
      _map(e);
    }
  }

  @override
  Future<void> index(String deviceId, List<IndexFilePayload> files) async {
    try {
      await api.post('/documents/index', {'device_id': deviceId, 'files': [for (final f in files) f.toJson()]});
    } catch (e) {
      _map(e);
    }
  }

  @override
  Future<int> remove(String deviceId, {List<String> paths = const [], String? prefix}) async {
    try {
      final r = await api.post('/documents/remove', {
        'device_id': deviceId,
        if (paths.isNotEmpty) 'paths': paths,
        'prefix': ?prefix,
      });
      return r is Map && r['removed'] is num ? (r['removed'] as num).toInt() : 0;
    } catch (e) {
      _map(e);
    }
  }

  @override
  Future<IndexServerStatus> status(String deviceId) async {
    try {
      final r = await api.get('/documents/status', query: {'device_id': deviceId});
      if (r is! Map) return const IndexServerStatus();
      return IndexServerStatus(
        files: (r['files'] as num?)?.toInt() ?? 0,
        chunks: (r['chunks'] as num?)?.toInt() ?? 0,
        indexingEnabled: r['indexing_enabled'] as bool?,
      );
    } catch (e) {
      _map(e);
    }
  }
}
