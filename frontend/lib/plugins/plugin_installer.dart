import 'dart:convert';
import 'dart:io';
import 'package:pai_mobile/plugins/models/plugin_manifest.dart';
import 'package:path/path.dart' as p;

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'package:pai_mobile/plugins/plugin_repository.dart';
import 'models/plugin_info.dart';
import 'package:archive/archive.dart';

class PluginInstaller {
  static const _tag = '[PluginInstaller]';

  static const _artifactName = 'package.tar.gz';
  static const _manifestName = 'manifest.json';
  static const _installedFileName = 'installed.json';

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
      debugPrint('$_tag Created plugins root: ${dir.path}');
    }

    return dir;
  }

  Future<Directory> pluginRoot(String id) async {
    _validatePathSegment(id, 'plugin id');

    final root = await pluginsRoot();
    return Directory('${root.path}/$id');
  }

  Future<Directory> pluginDir(String id, String version) async {
    _validatePathSegment(id, 'plugin id');
    _validatePathSegment(version, 'version');

    final root = await pluginRoot(id);

    return Directory('${root.path}/$version');
  }

  Future<File> artifactPath(String id, String version) async {
    final dir = await pluginDir(id, version);

    return File('${dir.path}/$_artifactName');
  }

  // ---------------------------------------------------------------
  // Discovery
  // ---------------------------------------------------------------

  Future<List<PluginManifest>> discover() async {
    debugPrint('$_tag discover() scanning plugin store');

    final root = await pluginsRoot();
    final out = <PluginManifest>[];

    await for (final idEntry in root.list()) {
      if (idEntry is! Directory) continue;

      final id = p.basename(idEntry.path);

      if (id.startsWith('.')) continue;

      await for (final versionEntry in idEntry.list()) {
        if (versionEntry is! Directory) continue;

        final version = p.basename(versionEntry.path);

        if (version.startsWith('.')) continue;

        final manifestFile = File(p.join(versionEntry.path, _manifestName));

        if (!await manifestFile.exists()) {
          debugPrint('$_tag Skipping ${versionEntry.path}: manifest missing');
          continue;
        }

        try {
          final decoded = jsonDecode(await manifestFile.readAsString());

          if (decoded is! Map) {
            throw const FormatException('Manifest root must be a JSON object');
          }

          final manifest = PluginManifest.fromJson(
            Map<String, dynamic>.from(decoded),
          );

          if (manifest.id != id || manifest.version != version) {
            debugPrint(
              '$_tag Skipping mismatched manifest: '
              '${manifest.id}@${manifest.version}',
            );
            continue;
          }

          out.add(manifest);

          debugPrint(
            '$_tag Found ${manifest.id}@${manifest.version} '
            'with ${manifest.capabilityDefinitions.length} capabilities: '
            '${manifest.capabilityDefinitions.map((c) => c.id).toList()}',
          );
        } catch (e) {
          debugPrint('$_tag Malformed manifest at ${manifestFile.path}: $e');
        }
      }
    }

    debugPrint('$_tag discover(): ${out.length} plugin(s) found');

    return out;
  }

  // ---------------------------------------------------------------
  // Install
  // ---------------------------------------------------------------

  Future<PluginInfo> install(String id, String version, {String? abi}) async {
    // 1. Download package.tar.gz
    //   ↓
    // 2. Verify SHA-256 checksum
    //   ↓
    // 3. Extract into temporary directory
    //   ↓
    //   temp/<version>/contents/
    //       linux/libpai_camera.so
    //   ↓
    // 4. Validate extracted files against manifest
    //   ↓
    // 5. Move existing installation to backup (if present)
    //   ↓
    // 6. Rename temporary installation to final directory
    //   ↓
    //   plugins/<plugin_id>/<version>/
    //       ├── package.tar.gz
    //       ├── manifest.json
    //       └── contents/
    //           └── linux/
    //               └── libpai_camera.so
    //   ↓
    // 7. Update installed.json
    //   ↓
    // 8. Remove backup

    _validatePathSegment(id, 'plugin id');
    _validatePathSegment(version, 'version');

    // 1. Fetch plugin catalog information.
    final pluginJson = await repository.fetchPlugin(id, version: version);

    final pluginInfo = PluginInfo.fromJson(pluginJson);

    if (pluginInfo.id != id || pluginInfo.version != version) {
      throw PluginRepositoryException(
        'Plugin identity/version mismatch. '
        'Requested $id@$version, received '
        '${pluginInfo.id}@${pluginInfo.version}',
      );
    }

    final packageUrl = pluginInfo.packageUrl;
    final checksum = pluginInfo.checksum;

    if (packageUrl == null || packageUrl.isEmpty) {
      throw PluginRepositoryException('Plugin package URL is missing');
    }

    if (checksum == null || checksum.isEmpty) {
      throw PluginRepositoryException('Plugin SHA-256 checksum is missing');
    }

    // 2. Prepare installation directories.
    final pluginDirectory = await pluginRoot(id);
    await pluginDirectory.create(recursive: true);

    final suffix = DateTime.now().microsecondsSinceEpoch;

    final tempDir = Directory('${pluginDirectory.path}/.$version.$suffix.tmp');

    final finalDir = Directory('${pluginDirectory.path}/$version');

    final backupDir = Directory(
      '${pluginDirectory.path}/.$version.$suffix.backup',
    );

    var previousInstallationBackedUp = false;
    var installationPromoted = false;
    var registryUpdated = false;

    try {
      // -----------------------------------------------------------
      // STEP 1: Create temporary directory.
      // -----------------------------------------------------------

      await tempDir.create(recursive: true);

      debugPrint('$_tag Temporary directory created: ${tempDir.path}');

      // -----------------------------------------------------------
      // STEP 2: Download artifact.
      // -----------------------------------------------------------

      final artifactFile = File('${tempDir.path}/$_artifactName');

      debugPrint('$_tag Downloading artifact to ${artifactFile.path}');

      await repository.downloadArtifact(
        url: packageUrl,
        expectedSha256: checksum,
        dest: artifactFile,
      );

      if (!await artifactFile.exists()) {
        throw PluginRepositoryException('Downloaded artifact does not exist');
      }

      final artifactSize = await artifactFile.length();

      if (artifactSize == 0) {
        throw PluginRepositoryException('Downloaded artifact is empty');
      }

      debugPrint('$_tag Download completed: $artifactSize bytes');

      // -----------------------------------------------------------
      // STEP 3: Verify SHA-256.
      // -----------------------------------------------------------

      final actualChecksum = await _calculateSha256(artifactFile);

      final expectedChecksum = _normalizeSha256(checksum);

      if (actualChecksum != expectedChecksum) {
        throw PluginRepositoryException(
          'SHA-256 verification failed. '
          'Expected: $expectedChecksum, '
          'Actual: $actualChecksum',
        );
      }

      debugPrint('$_tag SHA-256 verification successful');

      // Extract package.

      final contentsDirectory = Directory('${tempDir.path}/contents');

      await _extractPackage(
        archiveFile: artifactFile,
        destination: contentsDirectory,
      );

      debugPrint('$_tag Package extracted to ${contentsDirectory.path}');

      // -----------------------------------------------------------
      // STEP 4: Fetch and validate manifest.
      // -----------------------------------------------------------

      final manifest = await repository.fetchManifest(id, version: version);

      _validateManifest(manifest, id: id, version: version);

      // Parse it now to validate capability definitions before persisting.
      final parsedManifest = PluginManifest.fromJson(manifest);

      debugPrint(
        '$_tag Manifest contains '
        '${parsedManifest.capabilityDefinitions.length} capabilities',
      );

      final manifestFile = File('${tempDir.path}/$_manifestName');

      await manifestFile.writeAsString(jsonEncode(manifest), flush: true);

      debugPrint('$_tag Manifest validated and persisted');

      // -----------------------------------------------------------
      // STEP 5: Promote temporary directory.
      // -----------------------------------------------------------

      // Preserve the existing installation if one exists.
      if (await finalDir.exists()) {
        debugPrint('$_tag Backing up existing installation');

        await finalDir.rename(backupDir.path);

        previousInstallationBackedUp = true;
      }

      // Promote the temporary directory.
      await tempDir.rename(finalDir.path);

      debugPrint('$_tag Installation promoted to ${finalDir.path}');

      installationPromoted = true;

      debugPrint('$_tag Installation promoted to ${finalDir.path}');

      // -----------------------------------------------------------
      // STEP 6: Update installed.json.
      // -----------------------------------------------------------

      await _markInstalled(id, version, enabled: false);

      registryUpdated = true;

      debugPrint('$_tag Registry updated: $id@$version');
    } catch (e, stackTrace) {
      debugPrint('$_tag Installation failed: $e');
      debugPrintStack(stackTrace: stackTrace);

      // -----------------------------------------------------------
      // ROLLBACK
      // -----------------------------------------------------------

      // Remove incomplete temporary directory.
      if (await tempDir.exists()) {
        await tempDir.delete(recursive: true);
      }

      // Remove newly promoted installation if registry update failed.
      if (installationPromoted && !registryUpdated && await finalDir.exists()) {
        await finalDir.delete(recursive: true);
      }

      // Restore the previous installation.
      if (previousInstallationBackedUp && await backupDir.exists()) {
        await backupDir.rename(finalDir.path);

        debugPrint('$_tag Previous installation restored');
      }

      rethrow;
    }

    // Cleanup is deliberately outside the transaction.
    // A cleanup failure must not roll back a successful installation.
    if (previousInstallationBackedUp && await backupDir.exists()) {
      try {
        await backupDir.delete(recursive: true);

        debugPrint('$_tag Previous backup removed');
      } catch (e) {
        debugPrint('$_tag Warning: could not remove backup: $e');
      }
    }

    debugPrint('$_tag install($id, $version) completed successfully');

    return pluginInfo;
  }

  Future<PluginManifest> readManifest(String id, String version) async {
    final directory = await pluginDir(id, version);

    final file = File(p.join(directory.path, _manifestName));

    if (!await file.exists()) {
      throw PluginRepositoryException('Manifest not found for $id@$version');
    }

    final decoded = jsonDecode(await file.readAsString());

    if (decoded is! Map) {
      throw const FormatException('Manifest root must be a JSON object');
    }

    final manifest = PluginManifest.fromJson(
      Map<String, dynamic>.from(decoded),
    );

    if (manifest.id != id || manifest.version != version) {
      throw PluginRepositoryException(
        'Installed manifest identity mismatch for $id@$version',
      );
    }

    return manifest;
  }

  // ---------------------------------------------------------------
  // Remove
  // ---------------------------------------------------------------

  Future<void> remove(String id) async {
    _validatePathSegment(id, 'plugin id');

    debugPrint('$_tag remove($id) starting');

    final dir = await pluginRoot(id);

    if (await dir.exists()) {
      await dir.delete(recursive: true);

      debugPrint('$_tag Deleted ${dir.path}');
    }

    await _markRemoved(id);

    debugPrint('$_tag remove($id) completed');
  }

  /// Remove only one version of a plugin.
  Future<void> removeVersion(String id, String version) async {
    final dir = await pluginDir(id, version);

    if (await dir.exists()) {
      await dir.delete(recursive: true);
      debugPrint('$_tag Removed $id@$version');
    }
  }

  /// Restore the previous installed.json entry after a failed installation.
  Future<void> restoreInstalledEntry(
    String id,
    Map<String, dynamic>? previousEntry,
  ) async {
    final installed = await readInstalled();

    if (previousEntry == null) {
      installed.remove(id);
    } else {
      installed[id] = previousEntry;
    }

    await _writeInstalled(installed);

    debugPrint('$_tag Restored installed state for $id');
  }

  /// Resolves the actual native library inside an extracted plugin package.
  ///
  /// PlatformSpec.artifact is a package-relative path. It can contain:
  ///   {platform} - replaced with the target platform
  ///   {abi}      - replaced with the device ABI
  ///
  /// Example:
  ///   PlatformSpec.artifact = "native/{abi}/libplugin.so"
  ///
  /// Extracted package structure:
  ///   plugins/
  ///     plugin_id/
  ///       1.0.0/
  ///         package.tar.gz
  ///         contents/
  ///           native/
  ///             arm64-v8a/
  ///               libplugin.so
  Future<File> resolveNativeArtifact({
    required String id,
    required String version,
    required String platform,
    required String abi,
    required PlatformSpec platformSpec,
  }) async {
    // 1. Validate platform specification.
    if (platformSpec.platform != platform) {
      throw StateError(
        'Platform mismatch: expected $platform, '
        'but received ${platformSpec.platform}',
      );
    }

    // 2. Validate artifact declaration.
    final artifact = platformSpec.artifact.trim();

    if (artifact.isEmpty) {
      debugPrint(
        'No native artifact declared for plugin $id '
        'on platform $platform',
      );
      throw StateError(
        'No native artifact declared for plugin $id '
        'on platform $platform',
      );
    }

    if (abi.trim().isEmpty) {
      throw ArgumentError('Device ABI cannot be empty');
    }

    // 3. Resolve extracted package directory.
    final pluginDirectory = await pluginDir(id, version);
    final contentsDirectory = Directory(
      p.join(pluginDirectory.path, 'contents'),
    );

    if (!await contentsDirectory.exists()) {
      throw StateError(
        'Extracted package contents not found: '
        '${contentsDirectory.path}',
      );
    }

    // 4. Resolve platform and ABI placeholders.
    final relativeArtifact = artifact
        .replaceAll('{platform}', platform)
        .replaceAll('{abi}', abi)
        .replaceAll('\\', '/');

    // Reject absolute paths and path traversal.
    if (p.isAbsolute(relativeArtifact) ||
        RegExp(r'^[a-zA-Z]:').hasMatch(relativeArtifact)) {
      throw StateError('Artifact path must be relative: $relativeArtifact');
    }

    final segments = relativeArtifact.split('/');

    if (segments.any((segment) => segment == '..' || segment.isEmpty)) {
      throw StateError('Invalid artifact path: $relativeArtifact');
    }

    // 5. Resolve the artifact against the extracted directory.
    final contentsPath = await contentsDirectory.resolveSymbolicLinks();

    final artifactPath = p.joinAll([contentsPath, ...segments]);

    final artifactFile = File(artifactPath);

    if (!await artifactFile.exists()) {
      debugPrint(
        'Native artifact not found for plugin $id.\n'
        'Platform: $platform\n'
        'ABI: $abi\n'
        'Expected path: $artifactPath',
      );
      throw StateError(
        'Native artifact not found for plugin $id.\n'
        'Platform: $platform\n'
        'ABI: $abi\n'
        'Expected path: $artifactPath',
      );
    }

    // 6. Ensure the resolved file has not escaped the package directory.
    final resolvedArtifactPath = await artifactFile.resolveSymbolicLinks();

    if (!p.isWithin(contentsPath, resolvedArtifactPath)) {
      throw StateError(
        'Native artifact resolves outside the plugin package: '
        '$resolvedArtifactPath',
      );
    }

    return File(resolvedArtifactPath);
  }

  Future<void> _extractPackage({
    required File archiveFile,
    required Directory destination,
  }) async {
    debugPrint('$_tag Extracting ${archiveFile.path}');

    await destination.create(recursive: true);

    // Read and decompress the gzip archive.
    final compressedBytes = await archiveFile.readAsBytes();
    final tarBytes = GZipDecoder().decodeBytes(compressedBytes);

    // Decode TAR entries.
    final archive = TarDecoder().decodeBytes(tarBytes);

    final destinationPath = await destination.resolveSymbolicLinks();

    for (final entry in archive) {
      final name = entry.name.replaceAll('\\', '/');

      if (name.isEmpty || name.startsWith('/')) {
        throw PluginRepositoryException('Invalid archive entry path: $name');
      }

      final segments = name.split('/');

      if (segments.any(
        (segment) => segment == '..' || segment == '.' || segment.isEmpty,
      )) {
        // Allow a trailing slash for directory entries.
        final normalized = name.endsWith('/')
            ? name.substring(0, name.length - 1)
            : name;

        if (normalized
            .split('/')
            .any((segment) => segment == '..' || segment == '.')) {
          throw PluginRepositoryException(
            'Path traversal detected in archive: $name',
          );
        }
      }

      final outputPath = p.normalize(p.join(destinationPath, name));

      if (!p.isWithin(destinationPath, outputPath) &&
          outputPath != destinationPath) {
        throw PluginRepositoryException(
          'Archive entry escapes extraction directory: $name',
        );
      }

      if (entry.isSymbolicLink ||
          entry.isFile == false && entry.isDirectory == false) {
        throw FormatException('Unsupported archive entry: ${entry.name}');
      }

      if (entry.isSymbolicLink ||
          entry.isFile == false && entry.isDirectory == false) {
        throw PluginRepositoryException('Unsupported archive entry: $name');
      }

      if (entry.isDirectory) {
        await Directory(outputPath).create(recursive: true);
        continue;
      }

      if (entry.isFile) {
        final outputFile = File(outputPath);

        await outputFile.parent.create(recursive: true);

        final content = entry.content;

        await outputFile.writeAsBytes(content, flush: true);
      }
    }

    debugPrint('$_tag Extraction completed: ${destination.path}');
  }
  // ---------------------------------------------------------------
  // Enable / Disable
  // ---------------------------------------------------------------

  Future<void> setEnabled(String id, bool enabled) async {
    _validatePathSegment(id, 'plugin id');

    final installed = await readInstalled();
    final entry = installed[id];

    if (entry is! Map) {
      throw PluginRepositoryException('Plugin $id is not installed');
    }

    entry['enabled'] = enabled;

    await _writeInstalled(installed);

    debugPrint('$_tag setEnabled($id, $enabled)');
  }

  // ---------------------------------------------------------------
  // installed.json
  // ---------------------------------------------------------------

  Future<File> _installedFile() async {
    final root = await pluginsRoot();

    return File('${root.path}/$_installedFileName');
  }

  Future<Map<String, dynamic>> readInstalled() async {
    final file = await _installedFile();

    if (!await file.exists()) {
      debugPrint('$_tag installed.json not found');

      return <String, dynamic>{};
    }

    try {
      final decoded = jsonDecode(await file.readAsString());

      if (decoded is! Map<String, dynamic>) {
        throw const FormatException(
          'installed.json must contain a JSON object',
        );
      }

      return decoded;
    } catch (e) {
      debugPrint('$_tag installed.json is invalid: $e');

      throw PluginRepositoryException('Could not read installed.json: $e');
    }
  }

  Future<void> _markInstalled(
    String id,
    String version, {
    required bool enabled,
  }) async {
    final installed = await readInstalled();

    installed[id] = {'version': version, 'enabled': enabled};

    await _writeInstalled(installed);

    debugPrint('$_tag _markInstalled($id, $version, enabled=$enabled)');
  }

  Future<void> _markRemoved(String id) async {
    final installed = await readInstalled();

    installed.remove(id);

    await _writeInstalled(installed);

    debugPrint('$_tag _markRemoved($id)');
  }

  /// Writes installed.json through a temporary file.
  ///
  /// A backup is retained until the new file has been promoted.
  Future<void> _writeInstalled(Map<String, dynamic> data) async {
    final file = await _installedFile();

    final suffix = DateTime.now().microsecondsSinceEpoch;

    final tempFile = File('${file.path}.$suffix.tmp');
    final backupFile = File('${file.path}.$suffix.backup');

    var backedUp = false;

    try {
      // Write the complete new registry.
      await tempFile.writeAsString(jsonEncode(data), flush: true);

      // Preserve the previous registry.
      if (await file.exists()) {
        await file.rename(backupFile.path);
        backedUp = true;
      }

      // Promote the new registry.
      await tempFile.rename(file.path);

      // Remove the old registry after successful promotion.
      if (backedUp && await backupFile.exists()) {
        await backupFile.delete();
      }
    } catch (e) {
      // Remove incomplete new registry.
      if (await tempFile.exists()) {
        await tempFile.delete();
      }

      // Restore old registry if promotion failed.
      if (backedUp && await backupFile.exists() && !await file.exists()) {
        await backupFile.rename(file.path);
      }

      rethrow;
    }
  }

  // ---------------------------------------------------------------
  // Validation
  // ---------------------------------------------------------------

  void _validatePathSegment(String value, String label) {
    if (value.isEmpty ||
        value == '.' ||
        value == '..' ||
        value.contains('/') ||
        value.contains(r'\') ||
        value.contains('\u0000')) {
      throw PluginRepositoryException('Invalid $label: $value');
    }
  }

  void _validateManifest(
    Map<String, dynamic> manifest, {
    required String id,
    required String version,
  }) {
    if (manifest['id'] != id) {
      throw PluginRepositoryException(
        'Manifest plugin ID mismatch. '
        'Expected $id, received ${manifest['id']}',
      );
    }

    if (manifest['version'] != version) {
      throw PluginRepositoryException(
        'Manifest version mismatch. '
        'Expected $version, received ${manifest['version']}',
      );
    }
  }

  // ---------------------------------------------------------------
  // SHA-256
  // ---------------------------------------------------------------

  String _normalizeSha256(String checksum) {
    return checksum.trim().toLowerCase().replaceFirst(RegExp(r'^sha256:'), '');
  }

  Future<String> _calculateSha256(File file) async {
    final digest = await sha256.bind(file.openRead()).first;

    return digest.toString();
  }
}
