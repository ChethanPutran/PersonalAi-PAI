/// Release notes for one plugin version (from the manifest `changelog`).
class ChangelogEntry {
  final String version;
  final String summary;
  final List<String> features;
  final List<String> fixes;
  final List<String> breaking;

  const ChangelogEntry({
    required this.version,
    this.summary = '',
    this.features = const [],
    this.fixes = const [],
    this.breaking = const [],
  });

  bool get isEmpty =>
      summary.isEmpty && features.isEmpty && fixes.isEmpty && breaking.isEmpty;

  static List<String> _list(dynamic v) =>
      v is List ? v.map((e) => e.toString()).toList(growable: false) : const [];

  factory ChangelogEntry.fromJson(Map<String, dynamic> j) => ChangelogEntry(
        version: j['version']?.toString() ?? '',
        summary: j['summary']?.toString() ?? '',
        features: _list(j['features']),
        fixes: _list(j['fixes']),
        breaking: _list(j['breaking']),
      );
}

/// A newer version exists but cannot be used here (server or app too old).
class BlockedUpdate {
  final String version;
  final String? code;
  final String reason;
  final String? required;

  const BlockedUpdate({required this.version, this.code, required this.reason, this.required});

  factory BlockedUpdate.fromJson(Map<String, dynamic> j) => BlockedUpdate(
        version: j['version']?.toString() ?? '',
        code: j['code']?.toString(),
        reason: j['reason']?.toString() ?? 'Not compatible',
        required: j['required']?.toString(),
      );
}

/// Update status of one installed plugin on one device, as computed by the backend.
class PluginUpdate {
  final String pluginId;
  final String? installedVersion;

  /// Newest version this device can actually run (server AND app compatible).
  final String? latestVersion;
  final bool updateAvailable;
  final BlockedUpdate? blocked;

  /// Release notes of every version being skipped, oldest first.
  final List<ChangelogEntry> changelog;
  final List<String> addedCapabilities;
  final List<String> removedCapabilities;
  final List<String> changedCapabilities;

  const PluginUpdate({
    required this.pluginId,
    this.installedVersion,
    this.latestVersion,
    this.updateAvailable = false,
    this.blocked,
    this.changelog = const [],
    this.addedCapabilities = const [],
    this.removedCapabilities = const [],
    this.changedCapabilities = const [],
  });

  bool get hasNotes => changelog.any((c) => !c.isEmpty) ||
      addedCapabilities.isNotEmpty ||
      removedCapabilities.isNotEmpty ||
      changedCapabilities.isNotEmpty;

  factory PluginUpdate.fromJson(Map<String, dynamic> j) {
    final caps = j['capabilities'] is Map
        ? Map<String, dynamic>.from(j['capabilities'] as Map)
        : const <String, dynamic>{};
    List<String> l(dynamic v) =>
        v is List ? v.map((e) => e.toString()).toList(growable: false) : const [];
    return PluginUpdate(
      pluginId: j['plugin_id']?.toString() ?? '',
      installedVersion: j['installed_version']?.toString(),
      latestVersion: j['latest_version']?.toString(),
      updateAvailable: j['update_available'] == true,
      blocked: j['blocked'] is Map
          ? BlockedUpdate.fromJson(Map<String, dynamic>.from(j['blocked'] as Map))
          : null,
      changelog: (j['changelog'] as List? ?? const [])
          .whereType<Map>()
          .map((e) => ChangelogEntry.fromJson(Map<String, dynamic>.from(e)))
          .toList(growable: false),
      addedCapabilities: l(caps['added']),
      removedCapabilities: l(caps['removed']),
      changedCapabilities: l(caps['changed']),
    );
  }
}
