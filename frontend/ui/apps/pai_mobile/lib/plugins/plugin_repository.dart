import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:crypto/crypto.dart';

import 'models/plugin_info.dart';

class PluginRepository {
  final Uri baseUrl;
  final http.Client httpClient;

  PluginRepository({required this.baseUrl, http.Client? client})
      : httpClient = client ?? http.Client();

  Future<Map<String, dynamic>> fetchIndex() async {
    final r = await httpClient.get(baseUrl.resolve('plugins/index.json'));
    if (r.statusCode != 200) throw Exception('index fetch failed: ${r.statusCode}');
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  Future<PluginInfo> fetchManifest(String url) async {
    final r = await httpClient.get(Uri.parse(url));
    if (r.statusCode != 200) throw Exception('manifest fetch failed');
    return PluginInfo.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  /// Download an artifact to [dest] and verify SHA-256.
  Future<void> downloadArtifact({
    required String url,
    required String expectedSha256,
    required File dest,
  }) async {
    await dest.parent.create(recursive: true);
    final req = http.Request('GET', Uri.parse(url));
    final res = await httpClient.send(req);
    if (res.statusCode != 200) throw Exception('artifact fetch failed');

    final sink = dest.openWrite();
    final digest = AccumulatorSink<Digest>();
    final hasher = sha256.startChunkedConversion(digest);
    await for (final chunk in res.stream) {
      sink.add(chunk);
      hasher.add(chunk);
    }
    await sink.close();
    hasher.close();

    final got = digest.events.single.toString();
    if (got != expectedSha256) {
      await dest.delete();
      throw Exception('integrity check failed: $got != $expectedSha256');
    }
  }
}