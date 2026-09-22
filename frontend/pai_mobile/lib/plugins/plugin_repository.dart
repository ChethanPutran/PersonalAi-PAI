import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;

import 'models/plugin_info.dart';

class PluginRepository {
  final Uri baseUrl;
  final http.Client httpClient;

  PluginRepository({required this.baseUrl, http.Client? client})
      : httpClient = client ?? http.Client();

  Future<Map<String, dynamic>> fetchIndex() async {
    final uri = baseUrl.resolve('index.json');
    final r = await httpClient.get(uri);
    if (r.statusCode != 200) {
      throw PluginRepositoryException('index fetch failed: ${r.statusCode}');
    }
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  Future<PluginInfo> fetchManifest(String url) async {
    final r = await httpClient.get(Uri.parse(url));
    if (r.statusCode != 200) {
      throw PluginRepositoryException(
          'manifest fetch failed: ${r.statusCode}');
    }
    return PluginInfo.fromManifestJson(
      jsonDecode(r.body) as Map<String, dynamic>,
    );
  }

  Future<void> downloadArtifact({
    required String url,
    required String expectedSha256,
    required File dest,
  }) async {
    await dest.parent.create(recursive: true);

    final tmp = File('${dest.path}.part');
    final req = http.Request('GET', Uri.parse(url));
    final res = await httpClient.send(req);

    if (res.statusCode != 200) {
      throw PluginRepositoryException(
          'artifact fetch failed: ${res.statusCode}');
    }

    final sink = tmp.openWrite();
    final digestSink = _DigestSink();
    final hasher = sha256.startChunkedConversion(digestSink);

    try {
      await for (final chunk in res.stream) {
        sink.add(chunk);
        hasher.add(chunk);
      }
      await sink.close();
      hasher.close();
    } catch (e) {
      await sink.close();
      if (await tmp.exists()) await tmp.delete();
      rethrow;
    }

    final got = digestSink.value.toString();
    if (got != expectedSha256) {
      await tmp.delete();
      throw PluginRepositoryException(
        'integrity check failed: got $got expected $expectedSha256',
      );
    }

    if (await dest.exists()) await dest.delete();
    await tmp.rename(dest.path);
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
  void add(Digest data) => value = data;

  @override
  void close() {}
}