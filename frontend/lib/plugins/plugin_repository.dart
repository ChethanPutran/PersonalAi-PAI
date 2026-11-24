
import 'dart:async';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;

import '../core/api/api_client.dart';

class PluginRepository {
  final ApiClient api;

  PluginRepository({required this.api});

  Future<Map<String, dynamic>> fetchPlugin(String id, {String? version}) async {
    final result = await api.get(
      '/plugins/$id',
      query: version == null ? null : {'version': version},
    );

    if (result is! Map) {
      throw PluginRepositoryException(
        'Invalid plugin response: ${result.runtimeType}',
      );
    }

    return Map<String, dynamic>.from(result);
  }

  Future<Map<String, dynamic>> fetchManifest(String pluginId, {String? version}) async {
    final result = await api.get(
      '/plugins/$pluginId/manifest.json',
      query: version == null ? null : {'version': version},
    );

    if (result is! Map) {
      throw PluginRepositoryException(
        'Invalid manifest response: ${result.runtimeType}',
      );
    }

    return Map<String, dynamic>.from(result);
  }

  /// `GET /plugins/updates` — per device: installed plugins with a newer version.
  Future<Map<String, dynamic>> fetchUpdates() async {
    final result = await api.get('/plugins/updates');
    if (result is! Map) {
      throw PluginRepositoryException('Invalid updates response: ${result.runtimeType}');
    }
    return Map<String, dynamic>.from(result);
  }

  Future<void> downloadArtifact({
    required String url,
    required String expectedSha256,
    required File dest,
  }) async {
    await dest.parent.create(recursive: true);

    final tmp = File('${dest.path}.part');

    if (await tmp.exists()) {
      await tmp.delete();
    }

    final digestSink = _DigestSink();
    final hasher = sha256.startChunkedConversion(digestSink);

    IOSink? fileSink;

    try {
      final resolvedUrl = api.resolveUrl(url);
      final request = http.Request('GET', resolvedUrl);

      final response = await api.send(request);

      if (response.statusCode != 200) {
        throw PluginRepositoryException(
          'Artifact download failed: HTTP ${response.statusCode}',
        );
      }

      fileSink = tmp.openWrite();

      // ApiClient.send currently returns a fully buffered Response.
      // This writes the response bytes incrementally to disk, but the
      // response itself has already been buffered in memory.
      final bytes = response.bodyBytes;

      for (var offset = 0; offset < bytes.length; offset += 64 * 1024) {
        final end = (offset + 64 * 1024 < bytes.length)
            ? offset + 64 * 1024
            : bytes.length;

        final chunk = bytes.sublist(offset, end);

        fileSink.add(chunk);
        hasher.add(chunk);
      }

      await fileSink.flush();
      await fileSink.close();
      fileSink = null;

      hasher.close();

      final actualSha256 ="sha256:${digestSink.value}";

      if (actualSha256.toLowerCase() != expectedSha256.toLowerCase()) {
        throw PluginRepositoryException(
          'SHA-256 verification failed. '
          'Expected: $expectedSha256, '
          'Got: $actualSha256',
        );
      }

      if (await dest.exists()) {
        await dest.delete();
      }

      await tmp.rename(dest.path);
    } catch (e) {
      if (fileSink != null) {
        await fileSink.close();
      }

      if (await tmp.exists()) {
        await tmp.delete();
      }

      rethrow;
    }
  }
}

class PluginRepositoryException implements Exception {
  final String message;

  PluginRepositoryException(this.message);

  @override
  String toString() => 'PluginRepositoryException: $message';
}

class _DigestSink implements Sink<Digest> {
  late Digest value;

  @override
  void add(Digest data) {
    value = data;
  }

  @override
  void close() {}
}
