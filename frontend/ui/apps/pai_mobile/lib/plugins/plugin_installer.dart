import 'dart:convert';
import 'dart:io';
import 'package:path_provider/path_provider.dart';

import 'models/plugin_info.dart';
import 'plugin_repository.dart';

class PluginInstaller {
  final PluginRepository repository;
  PluginInstaller(this.repository);

  Future<Directory> _pluginsRoot() async {
    final base = await getApplicationSupportDirectory();
    final dir = Directory('${base.path}/plugins');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  Future<Directory> pluginDir(String id, String version) async {
    final root = await _pluginsRoot();
    return Directory('${root.path}/$id/$version');
  }

  Future<List<PluginInfo>> discover() async {
    final root = await _pluginsRoot();
    final out = <PluginInfo>[];
    if (!await root.exists()) return out;

    await for (final idDir in root.list()) {
      if (idDir is! Directory) continue;
      await for (final verDir in idDir.list()) {
        if (verDir is! Directory) continue;
        final f = File('${verDir.path}/manifest.json');
        if (!await f.exists()) continue;
        try {
          out.add(PluginInfo.fromJson(
            jsonDecode(await f.readAsString()) as Map<String, dynamic>,
          ));
        } catch (_) {/* skip */}
      }
    }
    return out;
  }

  /// Install by id+version from the repository.
  Future<PluginInfo> install(String id, String version) async {
    final index = await repository.fetchIndex();
    final entry = (index['plugins'] as List)
        .firstWhere((p) => p['id'] == id, orElse: () => throw Exception('not found'));
    final ver = entry['versions'][version] as Map<String, dynamic>;

    final manifest = await repository.fetchManifest(ver['manifestUrl'] as String);

    final platform = _currentPlatformKey();
    final artifactUrl = (ver['artifacts'] as Map)[platform] as String?;
    final sha = (ver['sha256'] as Map)[platform] as String?;
    if (artifactUrl == null || sha == null) {
      throw Exception('plugin does not support platform: $platform');
    }

    final dir = await pluginDir(id, version);
    await dir.create(recursive: true);

    // Persist manifest
    await File('${dir.path}/manifest.json')
        .writeAsString(jsonEncode(manifest.toJson()));

    // Download native artifact
    final artifactFile = File('${dir.path}/native/${_artifactName(platform, id)}');
    await repository.downloadArtifact(
      url: artifactUrl,
      expectedSha256: sha,
      dest: artifactFile,
    );

    // Mark active version
    await _setActiveVersion(id, version);
    return manifest;
  }

  Future<void> remove(String id) async {
    final root = await _pluginsRoot();
    final d = Directory('${root.path}/$id');
    if (await d.exists()) await d.delete(recursive: true);
    await _removeActive(id);
  }

  String _currentPlatformKey() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isLinux) return 'linux';
    if (Platform.isWindows) return 'windows';
    if (Platform.isMacOS) return 'macos';
    if (Platform.isIOS) return 'ios';
    throw UnsupportedError('unknown platform');
  }

  String _artifactName(String platform, String id) {
    switch (platform) {
      case 'android': return 'pai-$id-android.aar';
      case 'linux':   return 'libpai_${id}.so';
      case 'windows': return 'pai_$id.dll';
      case 'macos':   return 'Pai$id.framework';
      case 'ios':     return 'Pai$id.framework';
    }
    throw UnsupportedError(platform);
  }

  Future<void> _setActiveVersion(String id, String version) async {
    final root = await _pluginsRoot();
    final f = File('${root.path}/installed.json');
    final m = await f.exists()
        ? jsonDecode(await f.readAsString()) as Map<String, dynamic>
        : <String, dynamic>{};
    m[id] = {'version': version, 'enabled': false};
    await f.writeAsString(jsonEncode(m));
  }

  Future<void> _removeActive(String id) async {
    final root = await _pluginsRoot();
    final f = File('${root.path}/installed.json');
    if (!await f.exists()) return;
    final m = jsonDecode(await f.readAsString()) as Map<String, dynamic>;
    m.remove(id);
    await f.writeAsString(jsonEncode(m));
  }
}