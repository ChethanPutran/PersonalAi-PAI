import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:pai_mobile/plugins/plugin_repository.dart';
import 'package:path_provider/path_provider.dart';

import 'models/plugin_info.dart';

class PluginInstaller {
  static const _tag = '[PluginInstaller]';

  final PluginRepository repository;
  PluginInstaller(this.repository);

  // ---------------------------------------------------------------
  // Paths
  // ---------------------------------------------------------------

  Future<Directory> pluginsRoot() async {
    final base = await getApplicationSupportDirectory();
    final dir = Directory('${base.path}/plugins');
    if (!await dir.exists()) {
      await dir.create(recursive: true);
      debugPrint('$_tag created plugins root: ${dir.path}');
    }
    return dir;
  }

  Future<Directory> pluginDir(String id, String version) async {
    final root = await pluginsRoot();
    return Directory('${root.path}/$id/$version');
  }

  // ---------------------------------------------------------------
  // Discovery
  // ---------------------------------------------------------------

  Future<List<PluginInfo>> discover() async {
    debugPrint('$_tag discover() scanning plugin store');

    final root = await pluginsRoot();
    final out = <PluginInfo>[];
    if (!await root.exists()) {
      debugPrint('$_tag discover(): root does not exist');
      return out;
    }

    await for (final idEntry in root.list()) {
      if (idEntry is! Directory) continue;

      await for (final verEntry in idEntry.list()) {
        if (verEntry is! Directory) continue;

        final f = File('${verEntry.path}/manifest.json');
        if (!await f.exists()) {
          debugPrint('$_tag   skip ${verEntry.path} (no manifest.json)');
          continue;
        }

        try {
          final json =
              jsonDecode(await f.readAsString()) as Map<String, dynamic>;
          final info = PluginInfo.fromManifestJson(json);
          out.add(info);
          debugPrint(
              '$_tag   found ${info.id}@${info.version} at ${verEntry.path}');
        } catch (e) {
          debugPrint('$_tag   malformed manifest at ${f.path}: $e');
        }
      }
    }

    debugPrint('$_tag discover(): ${out.length} plugin(s) on disk');
    return out;
  }

  // ---------------------------------------------------------------
  // Install
  // ---------------------------------------------------------------

  Future<PluginInfo> install(String id, String version) async {
    debugPrint('$_tag install($id, $version) starting');

    // 1. Resolve entry from registry index.
    debugPrint('$_tag   step 1: fetching registry index');
    final index = await repository.fetchIndex();
    final plugins = (index['plugins'] as List).cast<Map<String, dynamic>>();
    debugPrint('$_tag   index contains ${plugins.length} plugin(s)');

    final entry = plugins.firstWhere(
      (p) => p['id'] == id,
      orElse: () =>
          throw PluginRepositoryException('plugin $id not in registry'),
    );

    final versions = (entry['versions'] as Map).cast<String, dynamic>();
    final ver = versions[version] as Map<String, dynamic>?;
    if (ver == null) {
      throw PluginRepositoryException('plugin $id has no version $version');
    }
    debugPrint('$_tag   resolved $id@$version from index');

    // 2. Fetch manifest.
    final manifestUrl = ver['manifestUrl'] as String;
    debugPrint('$_tag   step 2: fetching manifest from $manifestUrl');
    final manifest = await repository.fetchManifest(manifestUrl);
    debugPrint(
        '$_tag   manifest: nativeModule=${manifest.nativeModule} '
        'capabilities=${manifest.capabilities}');

    // 3. Pick artifact for this platform.
    final platform = _platformKey();
    debugPrint('$_tag   step 3: resolving platform artifact ($platform)');

    final spec = manifest.platforms[platform];
    if (spec == null) {
      throw PluginRepositoryException(
          'plugin $id does not support $platform '
          '(available: ${manifest.platforms.keys.join(", ")})');
    }

    final artifactUrl = (ver['artifacts'] as Map)[platform] as String?;
    final sha = (ver['sha256'] as Map)[platform] as String?;
    if (artifactUrl == null || sha == null) {
      throw PluginRepositoryException(
          'registry missing artifact/sha for $id@$version on $platform');
    }
    debugPrint('$_tag   artifact: $artifactUrl');
    debugPrint('$_tag   expected sha256: $sha');

    // 4. Create version directory.
    final dir = await pluginDir(id, version);
    await dir.create(recursive: true);
    debugPrint('$_tag   step 4: plugin dir = ${dir.path}');

    // 5. Persist manifest.
    final manifestFile = File('${dir.path}/manifest.json');
    await manifestFile.writeAsString(jsonEncode(manifest.toJson()));
    debugPrint('$_tag   step 5: wrote ${manifestFile.path}');

    // 6. Download + verify artifact.
    final artifactFile = File('${dir.path}/${spec.artifact}');
    debugPrint('$_tag   step 6: downloading to ${artifactFile.path}');
    final t0 = DateTime.now();
    try {
      await repository.downloadArtifact(
        url: artifactUrl,
        expectedSha256: sha,
        dest: artifactFile,
      );
    } catch (e) {
      debugPrint('$_tag   download/verify FAILED: $e');
      rethrow;
    }
    final elapsed = DateTime.now().difference(t0);
    final size = await artifactFile.length();
    debugPrint(
        '$_tag   download ok: $size bytes in ${elapsed.inMilliseconds}ms');

    // 7. Mark installed.
    await _markInstalled(id, version, enabled: false);
    debugPrint('$_tag   step 7: marked installed (enabled=false)');

    debugPrint('$_tag install($id, $version) complete');
    return manifest;
  }

  // ---------------------------------------------------------------
  // Remove
  // ---------------------------------------------------------------

  Future<void> remove(String id) async {
    debugPrint('$_tag remove($id) starting');

    final root = await pluginsRoot();
    final dir = Directory('${root.path}/$id');
    if (await dir.exists()) {
      await dir.delete(recursive: true);
      debugPrint('$_tag   deleted ${dir.path}');
    } else {
      debugPrint('$_tag   no directory at ${dir.path}');
    }

    await _markRemoved(id);
    debugPrint('$_tag remove($id) complete');
  }

  // ---------------------------------------------------------------
  // Artifact path resolution
  // ---------------------------------------------------------------

  Future<String> artifactPath(
    String id,
    String version,
    String platform,
    PlatformSpec spec,
  ) async {
    final dir = await pluginDir(id, version);
    final p = '${dir.path}/${spec.artifact}';
    debugPrint('$_tag artifactPath($id@$version, $platform) = $p');

    // Warn early if the file isn't actually there.
    if (!File(p).existsSync()) {
      debugPrint('$_tag   WARNING: artifact file does not exist at $p');
    }
    return p;
  }

  // ---------------------------------------------------------------
  // installed.json
  // ---------------------------------------------------------------

  Future<File> _installedFile() async {
    final root = await pluginsRoot();
    return File('${root.path}/installed.json');
  }

  Future<Map<String, dynamic>> readInstalled() async {
    final f = await _installedFile();
    if (!await f.exists()) {
      debugPrint('$_tag installed.json not found');
      return {};
    }
    try {
      final m = jsonDecode(await f.readAsString()) as Map<String, dynamic>;
      debugPrint('$_tag installed.json = $m');
      return m;
    } catch (e) {
      debugPrint('$_tag installed.json parse error: $e');
      return {};
    }
  }

  Future<void> setEnabled(String id, bool enabled) async {
    final m = await readInstalled();
    final entry = m[id];
    if (entry is Map) {
      entry['enabled'] = enabled;
    } else {
      m[id] = {'version': '0.0.0', 'enabled': enabled};
    }
    await (await _installedFile()).writeAsString(jsonEncode(m));
    debugPrint('$_tag setEnabled($id, $enabled)');
  }

  Future<void> _markInstalled(
    String id,
    String version, {
    required bool enabled,
  }) async {
    final m = await readInstalled();
    m[id] = {'version': version, 'enabled': enabled};
    await (await _installedFile()).writeAsString(jsonEncode(m));
    debugPrint('$_tag _markInstalled($id, $version, enabled=$enabled)');
  }

  Future<void> _markRemoved(String id) async {
    final m = await readInstalled();
    m.remove(id);
    await (await _installedFile()).writeAsString(jsonEncode(m));
    debugPrint('$_tag _markRemoved($id)');
  }

  // ---------------------------------------------------------------

  String _platformKey() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isLinux) return 'linux';
    if (Platform.isWindows) return 'windows';
    if (Platform.isMacOS) return 'macos';
    if (Platform.isIOS) return 'ios';
    throw PluginRepositoryException('unknown platform');
  }
}