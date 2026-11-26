import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/api/api_client.dart';

/// One tap-to-add folder in a `paths` setting (`x-pai.suggestions`).
class SettingSuggestion {
  final String label;
  final String path;
  const SettingSuggestion(this.label, this.path);
}

enum SettingKind { boolean, string, integer, number, stringList, unsupported }

/// One setting of a plugin (a property of its manifest `configSchema`).
class SettingField {
  final String key;
  final SettingKind kind;
  final String title;
  final String description;
  final Object? defaultValue;

  /// `x-pai.widget` (for example `paths`).
  final String? widget;
  final List<SettingSuggestion> suggestions;
  final int? maxItems;
  final List<String> options;
  final num? minimum;
  final num? maximum;

  const SettingField({
    required this.key,
    required this.kind,
    required this.title,
    this.description = '',
    this.defaultValue,
    this.widget,
    this.suggestions = const [],
    this.maxItems,
    this.options = const [],
    this.minimum,
    this.maximum,
  });

  bool get isPaths => kind == SettingKind.stringList && widget == 'paths';

  /// "Camel case key" as words, for a setting that declares no title.
  static String humanize(String key) {
    final spaced = key.replaceAllMapped(RegExp(r'([a-z0-9])([A-Z])'), (m) => '${m[1]} ${m[2]}').replaceAll('_', ' ');
    return spaced.isEmpty ? key : '${spaced[0].toUpperCase()}${spaced.substring(1)}';
  }

  factory SettingField.fromJson(String key, Map<String, dynamic> j) {
    final type = j['type']?.toString();
    var kind = switch (type) {
      'boolean' => SettingKind.boolean,
      'string' => SettingKind.string,
      'integer' => SettingKind.integer,
      'number' => SettingKind.number,
      'array' => SettingKind.stringList,
      _ => SettingKind.unsupported,
    };
    final xpai = j['x-pai'] is Map ? Map<String, dynamic>.from(j['x-pai'] as Map) : const <String, dynamic>{};
    final sug = <SettingSuggestion>[];
    if (xpai['suggestions'] is List) {
      for (final s in xpai['suggestions'] as List) {
        if (s is Map && s['path'] != null) {
          sug.add(SettingSuggestion((s['label'] ?? s['path']).toString(), s['path'].toString()));
        }
      }
    }
    return SettingField(
      key: key,
      kind: kind,
      title: (j['title']?.toString().trim().isNotEmpty ?? false) ? j['title'].toString() : humanize(key),
      description: j['description']?.toString() ?? '',
      defaultValue: j['default'],
      widget: xpai['widget']?.toString(),
      suggestions: sug,
      maxItems: (j['maxItems'] as num?)?.toInt(),
      options: j['enum'] is List ? (j['enum'] as List).map((e) => e.toString()).toList() : const [],
      minimum: j['minimum'] as num?,
      maximum: j['maximum'] as num?,
    );
  }
}

/// The settings a plugin declares plus what the backend knows for ONE device:
/// `GET /plugins/device/{device}/{plugin}/config` -> {schema, values, defaults, effective}.
class PluginSettings {
  final List<SettingField> fields;

  /// What the user saved (a subset of the keys).
  final Map<String, dynamic> values;
  final Map<String, dynamic> defaults;

  /// Defaults overlaid with [values]: what the plugin actually uses.
  final Map<String, dynamic> effective;

  const PluginSettings({
    this.fields = const [],
    this.values = const {},
    this.defaults = const {},
    this.effective = const {},
  });

  bool get hasSettings => fields.isNotEmpty;

  factory PluginSettings.fromJson(Object? json) {
    if (json is! Map) return const PluginSettings();
    final schema = json['schema'];
    final props = schema is Map && schema['properties'] is Map ? Map<String, dynamic>.from(schema['properties'] as Map) : const <String, dynamic>{};
    final fields = <SettingField>[
      for (final e in props.entries)
        if (e.value is Map) SettingField.fromJson(e.key, Map<String, dynamic>.from(e.value as Map)),
    ];
    Map<String, dynamic> m(Object? v) => v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};
    return PluginSettings(fields: fields, values: m(json['values']), defaults: m(json['defaults']), effective: m(json['effective']));
  }
}

/// A settings request the server (or the device) refused, in words the user can act on.
class PluginSettingsException implements Exception {
  /// Short summary, shown above the form.
  final String message;

  /// Per setting: what is wrong with it.
  final Map<String, String> fieldErrors;

  /// The target device has remote install switched off (403).
  final bool remoteOff;

  const PluginSettingsException(this.message, {this.fieldErrors = const {}, this.remoteOff = false});

  @override
  String toString() => message;

  /// Maps an API failure: 422 `invalid_plugin_config` -> per-field errors, 403 -> "remote install is off".
  static PluginSettingsException fromApi(Object error) {
    if (error is ApiException) {
      final body = error.body;
      final detail = body is Map ? body['detail'] : null;
      if (error.statusCode == 422 && detail is Map && detail['errors'] is Map) {
        final errs = {for (final e in (detail['errors'] as Map).entries) e.key.toString(): e.value.toString()};
        return PluginSettingsException('Some settings need fixing.', fieldErrors: errs);
      }
      if (error.statusCode == 403) {
        final text = detail is Map
            ? detail['message']?.toString()
            : (detail is String ? detail : null);
        return PluginSettingsException(
          text ?? 'That device has remote plugin install switched off, so its settings cannot be changed from here.',
          remoteOff: true,
        );
      }
      if (error.statusCode == 404) return const PluginSettingsException('That plugin or device was not found.');
      if (detail is String && detail.isNotEmpty) return PluginSettingsException(detail);
      if (detail is Map && detail['message'] != null) return PluginSettingsException(detail['message'].toString());
    }
    return const PluginSettingsException("Couldn't reach the server. Check your connection and try again.");
  }
}

/// The effective settings of each plugin installed on THIS device, kept locally so the plugin gets them at
/// load time (also offline, before the app has reached the server).
class PluginConfigStore {
  static const _prefix = 'plugin_config.';

  Future<Map<String, dynamic>?> get(String pluginId) async {
    try {
      final raw = (await SharedPreferences.getInstance()).getString('$_prefix$pluginId');
      if (raw == null) return null;
      final v = jsonDecode(raw);
      return v is Map ? Map<String, dynamic>.from(v) : null;
    } catch (e) {
      debugPrint('[PluginConfigStore] read $pluginId failed: $e');
      return null;
    }
  }

  Future<void> save(String pluginId, Map<String, dynamic> config) async {
    try {
      await (await SharedPreferences.getInstance()).setString('$_prefix$pluginId', jsonEncode(config));
    } catch (e) {
      debugPrint('[PluginConfigStore] write $pluginId failed: $e');
    }
  }

  Future<void> forget(String pluginId) async {
    try {
      await (await SharedPreferences.getInstance()).remove('$_prefix$pluginId');
    } catch (_) {}
  }
}
