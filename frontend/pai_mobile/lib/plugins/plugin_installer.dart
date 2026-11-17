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
  Future<PluginInfo> install(
  String id,
  String version, {
  String abi = 'unknown',
}) async {
  debugPrint('$_tag install($id, $version, abi=$abi) starting');

  // 1. Resolve entry from registry index.
  final index = await repository.fetchIndex();
  final plugins = (index['plugins'] as List).cast<Map<String, dynamic>>();
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

  // 2. Fetch manifest.
  final platform = _platformKey();

  final manifestUrls =
      (ver['manifestUrls'] as Map?)?.cast<String, dynamic>() ?? {};
  final manifestUrl =
      manifestUrls[platform] as String?
      ?? (ver['manifestUrl'] as String?);

  if (manifestUrl == null) {
    throw PluginRepositoryException(
      'plugin $id@$version has no manifest for platform $platform',
    );
  }

  final manifest = await repository.fetchManifest(manifestUrl);

  // 3. Pick artifact for this platform + ABI.
  final spec = manifest.platforms[platform];
  if (spec == null) {
    throw PluginRepositoryException(
        'plugin $id does not support $platform '
        '(available: ${manifest.platforms.keys.join(", ")})');
  }

  final (artifactUrl, sha, usesAbi) = _pickArtifactUrl(
    ver: ver,
    platform: platform,
    abi: abi,
    pluginId: id,
    version: version,
  );
  debugPrint('$_tag   artifact: $artifactUrl');
  debugPrint('$_tag   expected sha256: $sha');
  debugPrint('$_tag   usesAbiPath: $usesAbi');

  // 4. Create version directory.
  final dir = await pluginDir(id, version);
  await dir.create(recursive: true);

  // 5. Persist manifest (unchanged).
  final manifestFile = File('${dir.path}/manifest.json');
  await manifestFile.writeAsString(jsonEncode(manifest.toJson()));

  // 6. Download to the ABI-correct local path.
  final localRel = _resolveLocalArtifactPath(
    declared: spec.artifact,
    platform: platform,
    abi: abi,
    usesAbiPath: usesAbi,
  );
  final artifactFile = File('${dir.path}/$localRel');
  await artifactFile.parent.create(recursive: true);
  debugPrint('$_tag   local path: ${artifactFile.path}');

  final t0 = DateTime.now();
  try {
    await repository.downloadArtifact(
      url: artifactUrl,        // ← ABI-aware URL now
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
  return manifest;
}

/// Resolve the download URL, sha256, and whether the URL is ABI-specific.
///
/// Prefers `artifactsByAbi[platform][abi]` when present (new backend).
/// Falls back to `artifacts[platform]` + appended `/$abi` (legacy backend).
( String, String, bool ) _pickArtifactUrl({
  required Map<String, dynamic> ver,
  required String platform,
  required String abi,
  required String pluginId,
  required String version,
}) {
  final artifacts = (ver['artifacts'] as Map?)?.cast<String, dynamic>() ?? {};
  final sha = (ver['sha256'] as Map?)?.cast<String, dynamic>() ?? {};
  final artifactsByAbi =
      (ver['artifactsByAbi'] as Map?)?.cast<String, dynamic>() ?? {};
  final shaByAbi =
      (ver['sha256ByAbi'] as Map?)?.cast<String, dynamic>() ?? {};

  final platformAbis =
      (artifactsByAbi[platform] as Map?)?.cast<String, dynamic>();
  final platformSha =
      (shaByAbi[platform] as Map?)?.cast<String, dynamic>();

  // Path 1: new backend, exact ABI match
  if (platformAbis != null && platformAbis.containsKey(abi)) {
    return (
      platformAbis[abi] as String,
      platformSha![abi] as String,
      true,
    );
  }

  // Path 2: new backend, no exact match — fall back to any declared
  // ABI and warn. Shouldn't happen if the manifest's `architectures`
  // is correct, but better to install *something* than fail.
  if (platformAbis != null && platformAbis.isNotEmpty) {
    final fallback = platformAbis.keys.first;
    debugPrint(
      '$_tag   WARN: no ABI-specific artifact for "$abi", '
      'falling back to "$fallback"',
    );
    return (
      platformAbis[fallback] as String,
      platformSha![fallback] as String,
      true,
    );
  }

  // Path 3: legacy backend — single flat artifact URL per platform.
  final baseUrl = artifacts[platform] as String?;
  final baseSha = sha[platform] as String?;
  if (baseUrl == null || baseSha == null) {
    throw PluginRepositoryException(
      'registry has no artifact for $pluginId@$version on $platform',
    );
  }

  // Legacy backend's URL is already platform-scoped but ABI-blind.
  // The old `registry_artifact` route accepted `abi` only as a query
  // param — appending to the path won't work. Return the flat URL
  // and let the legacy route serve its default ABI.
  return (baseUrl, baseSha, false);
}

/// Compute the on-disk path for the artifact.
///
/// Uses `spec.artifact` as the base. If the platform ships per-ABI
/// binaries AND the declared path is generic (`android/libpai_camera.so`),
/// insert the ABI directory. If the manifest already has ABI baked
/// into the path (`android/arm64-v8a/libpai_camera.so`), leave it.
String _resolveLocalArtifactPath({
  required String declared,
  required String platform,
  required String abi,
  required bool usesAbiPath,
}) {
  if (!usesAbiPath) return declared;

  const knownAbis = {'arm64-v8a', 'armeabi-v7a', 'x86_64', 'x86', 'arm64'};
  final parts = declared.split('/');

  // ABI already in the path → use as-is
  if (parts.any((p) => knownAbis.contains(p))) {
    return declared;
  }

  // Insert: android/libpai_camera.so → android/x86_64/libpai_camera.so
  if (parts.length == 2) {
    return '${parts[0]}/$abi/${parts[1]}';
  }

  // Unexpected shape — don't guess
  return declared;
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

/// Locate the artifact on disk.
///
/// Tries the ABI-specific path first (`android/x86_64/libpai_camera.so`),
/// then falls back to the manifest's declared path
/// (`android/libpai_camera.so`). This handles both layouts:
///   • new backend — downloaded to per-ABI subdirectory
///   • legacy backend — no ABI subdirectory on disk
///
/// Checking disk (rather than tracking ABI state) means the caller
/// doesn't need to know which download path was used — it just asks
/// "where is the file?" and gets back something that exists.
Future<String> artifactPath(
  String id,
  String version,
  String platform,
  PlatformSpec spec, {
  String abi = 'unknown',
}) async {
  final dir = await pluginDir(id, version);

  // 1. Try ABI-specific path first (what the new backend downloads to).
  if (abi != 'unknown' && abi.isNotEmpty) {
    final abiRel = _resolveLocalArtifactPath(
      declared: spec.artifact,
      platform: platform,
      abi: abi,
      usesAbiPath: true,
    );
    final abiPath = File('${dir.path}/$abiRel');
    if (abiPath.existsSync()) {
      debugPrint(
        '$_tag artifactPath($id@$version, $abi) = ${abiPath.path}',
      );
      return abiPath.path;
    }
  }

  // 2. Fall back to the manifest's declared path.
  final declared = '${dir.path}/${spec.artifact}';
  debugPrint('$_tag artifactPath($id@$version) = $declared');

  if (!File(declared).existsSync()) {
    // Neither path exists. Before failing, try one more thing:
    // a directory scan for any `.so`/`.dll`/`.dylib` under this plugin dir.
    // Handles manifests whose `artifact` was manually edited post-install.
    final scan = _findAnyArtifactSync(dir);
    if (scan != null) {
      debugPrint('$_tag   fallback scan found ${scan.path}');
      return scan.path;
    }
    debugPrint('$_tag   WARNING: artifact file does not exist at $declared '
        '(and no fallback found)');
  }
  return declared;
}

/// Best-effort scan for the first native artifact under `dir`.
File? _findAnyArtifactSync(Directory dir) {
  const exts = {'.so', '.dll', '.dylib'};
  try {
    for (final entity in dir.listSync(recursive: true, followLinks: false)) {
      if (entity is! File) continue;
      final p = entity.path;
      if (exts.any(p.endsWith)) return entity;
    }
  } catch (_) {}
  return null;
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
