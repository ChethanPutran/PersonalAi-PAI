import 'package:flutter/foundation.dart';

/// Small key/value store for user + per-device settings.
///
/// In-memory by default. To persist across restarts, pass an `initial`
/// map loaded from `SharedPreferences` (or any other store) and call
/// [onChanged] to write back — the API surface is intentionally small
/// so swapping in persistence is a one-file change.
class SettingsService extends ChangeNotifier {
  final Map<String, bool> _values;
  final Future<void> Function(Map<String, bool> values)? _onChanged;

  SettingsService({
    Map<String, bool>? initial,
    Future<void> Function(Map<String, bool> values)? onChanged,
  })  : _values = Map.of(initial ?? const {}),
        _onChanged = onChanged;

  Map<String, bool> export() => Map.unmodifiable(_values);

  bool getBool(String key, {bool defaultValue = true}) =>
      _values[key] ?? defaultValue;

  Future<void> setBool(String key, bool value) async {
    if (_values[key] == value) return;
    _values[key] = value;
    notifyListeners();
    await _onChanged?.call(_values);
  }

  // ---- Global account settings ----

  bool get notificationsEnabled => getBool('notifications');
  Future<void> setNotificationsEnabled(bool v) => setBool('notifications', v);

  bool get autoSyncEnabled => getBool('auto_sync');
  Future<void> setAutoSyncEnabled(bool v) => setBool('auto_sync', v);

  bool get backgroundLocationEnabled =>
      getBool('background_location', defaultValue: false);
  Future<void> setBackgroundLocationEnabled(bool v) =>
      setBool('background_location', v);

  // ---- Per-device settings ----

  bool getDeviceNotifications(String deviceId) =>
      getBool('device.$deviceId.notifications');

  Future<void> setDeviceNotifications(String deviceId, bool v) =>
      setBool('device.$deviceId.notifications', v);

  bool getDeviceAutoConnect(String deviceId) =>
      getBool('device.$deviceId.auto_connect');

  Future<void> setDeviceAutoConnect(String deviceId, bool v) =>
      setBool('device.$deviceId.auto_connect', v);

  /// Drop all settings belonging to a removed device.
  Future<void> forgetDevice(String deviceId) async {
    final prefix = 'device.$deviceId.';
    _values.removeWhere((k, _) => k.startsWith(prefix));
    notifyListeners();
    await _onChanged?.call(_values);
  }
}