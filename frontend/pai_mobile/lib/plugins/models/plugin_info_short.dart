import 'package:flutter/foundation.dart';

enum PluginState {
  available,
  installing,
  installed,
  enabled,
  disabled,
  error,
}

PluginState pluginStateFromString(String? value) {
  switch (value) {
    case 'available':
      return PluginState.available;

    case 'installing':
      return PluginState.installing;

    case 'installed':
      return PluginState.installed;

    case 'enabled':
      return PluginState.enabled;

    case 'disabled':
      return PluginState.disabled;

    case 'error':
      return PluginState.error;

    default:
      return PluginState.available;
  }
}

String pluginStateToString(PluginState state) {
  switch (state) {
    case PluginState.available:
      return 'available';

    case PluginState.installing:
      return 'installing';

    case PluginState.installed:
      return 'installed';

    case PluginState.enabled:
      return 'enabled';

    case PluginState.disabled:
      return 'disabled';

    case PluginState.error:
      return 'error';
  }
}

class PluginInfo extends ChangeNotifier {
  final String id;
  final String name;
  final String version;

  final List<String> platforms;
  final List<String> architectures;
  final List<String> capabilities;

  final String? packageUrl;
  final String? checksum;
  final int size;

  PluginState _state;

  String? _error;

  PluginInfo({
    required this.id,
    required this.name,
    required this.version,
    required this.platforms,
    required this.architectures,
    required this.capabilities,
    this.packageUrl,
    this.checksum,
    this.size = 0,
    PluginState state = PluginState.available,
    String? error,
  })  : _state = state,
        _error = error;

  PluginState get state => _state;

  String? get error => _error;

  bool get isAvailable =>
      _state == PluginState.available;

  bool get isInstalling =>
      _state == PluginState.installing;

  bool get isInstalled =>
      _state == PluginState.installed ||
      _state == PluginState.enabled ||
      _state == PluginState.disabled;

  bool get isEnabled =>
      _state == PluginState.enabled;

  bool get isDisabled =>
      _state == PluginState.disabled;

  bool get hasError =>
      _state == PluginState.error;

  void setState(PluginState state) {
    _state = state;
    notifyListeners();
  }

  void setError(String error) {
    _error = error;
    _state = PluginState.error;
    notifyListeners();
  }

  void clearError() {
    _error = null;
    notifyListeners();
  }

  factory PluginInfo.fromJson(
    Map<String, dynamic> json,
  ) {
    return PluginInfo(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      version: json['version']?.toString() ?? '1.0.0',

      platforms: json['platforms'] is List
          ? List<String>.from(
              (json['platforms'] as List)
                  .map((e) => e.toString()),
            )
          : const [],

      architectures: json['architectures'] is List
          ? List<String>.from(
              (json['architectures'] as List)
                  .map((e) => e.toString()),
            )
          : const [],

      capabilities: json['capabilities'] is List
          ? List<String>.from(
              (json['capabilities'] as List)
                  .map((e) => e.toString()),
            )
          : const [],

      packageUrl: json['package_url']?.toString(),

      checksum: json['checksum']?.toString(),

      size: json['size'] is num
          ? (json['size'] as num).toInt()
          : 0,

      state: pluginStateFromString(
        json['state']?.toString(),
      ),

      error: json['error']?.toString(),
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'name': name,
      'version': version,
      'platforms': platforms,
      'architectures': architectures,
      'capabilities': capabilities,
      'package_url': packageUrl,
      'checksum': checksum,
      'size': size,
      'state': pluginStateToString(_state),
      'error': _error,
    };
  }

  @override
  String toString() {
    return 'PluginInfo('
        'id=$id, '
        'version=$version, '
        'state=$_state'
        ')';
  }
}