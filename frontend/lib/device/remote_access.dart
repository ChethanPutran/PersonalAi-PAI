import 'package:flutter/foundation.dart';

import '../core/api/api_client.dart';

/// What the owner lets OTHER devices (and the web, schedules, agents) do to THIS device.
/// Both are off by default; nothing is allowed until the backend says so (fail closed).
@immutable
class RemoteAccess {
  final bool install;
  final bool invoke;

  const RemoteAccess({this.install = false, this.invoke = false});

  static const none = RemoteAccess();

  /// Parses `{install, invoke}` (WebSocket frames) or `{allow_remote_install, allow_remote_invoke}` (REST).
  /// Anything that is not a map, or a value that is not exactly `true`, means "not allowed".
  factory RemoteAccess.fromJson(Object? raw) {
    if (raw is! Map) return none;
    return RemoteAccess(
      install: (raw['install'] ?? raw['allow_remote_install']) == true,
      invoke: (raw['invoke'] ?? raw['allow_remote_invoke']) == true,
    );
  }

  RemoteAccess copyWith({bool? install, bool? invoke}) =>
      RemoteAccess(install: install ?? this.install, invoke: invoke ?? this.invoke);

  @override
  bool operator ==(Object other) =>
      other is RemoteAccess && other.install == install && other.invoke == invoke;

  @override
  int get hashCode => Object.hash(install, invoke);
}

const String kRemoteUseOffMessage = 'Remote plugin use is turned off on this device.';
const String kRemoteInstallOffMessage = 'Remote plugin install is turned off on this device.';

/// Builtins the server schedules itself (reminders). They are not plugin capabilities, so the
/// remote-use switch does not apply to them (otherwise every reminder would stop with it off).
const Set<String> kRemoteGateExempt = {'system.notifications.show'};

/// True when a request did not come from this device itself (no source, or another device).
bool isRemoteSource(String? sourceDeviceId, String? thisDeviceId) =>
    sourceDeviceId == null ||
    sourceDeviceId.isEmpty ||
    thisDeviceId == null ||
    sourceDeviceId != thisDeviceId;

/// Why a plugin install/uninstall command must be refused, or `null` to run it.
String? remoteInstallRefusal({
  required String? requestedBy,
  required String? thisDeviceId,
  required RemoteAccess access,
}) =>
    isRemoteSource(requestedBy, thisDeviceId) && !access.install ? kRemoteInstallOffMessage : null;

/// Why an invoke frame must be refused, or `null` to run it.
String? remoteInvokeRefusal({
  required String capability,
  required String? sourceDeviceId,
  required String? thisDeviceId,
  required RemoteAccess access,
}) {
  if (kRemoteGateExempt.contains(capability)) return null;
  return isRemoteSource(sourceDeviceId, thisDeviceId) && !access.invoke ? kRemoteUseOffMessage : null;
}

/// This device's remote-access switches: pushed by the backend (the `connected` frame, then
/// `device.remote_access` whenever the owner changes them) and changed by the owner through
/// `PUT /devices/{id}/remote-access`. The connection service enforces it on every invoke.
class RemoteAccessController extends ChangeNotifier {
  RemoteAccessController({this.api, this.deviceId});

  final ApiClient? api;
  final String? deviceId;

  RemoteAccess _value = RemoteAccess.none;
  bool _known = false;
  bool _saving = false;
  String? _error;

  RemoteAccess get value => _value;

  /// False until the backend has told us (the switches show "off" and are not trusted before).
  bool get known => _known;
  bool get saving => _saving;
  String? get error => _error;

  /// Applies a WebSocket frame's `remote_access` object.
  void applyFrame(Object? raw) {
    if (raw is! Map) return;
    _set(RemoteAccess.fromJson(raw));
  }

  /// Back to fail-closed (logout / reconnect to another account).
  void reset() {
    _value = RemoteAccess.none;
    _known = false;
    _saving = false;
    _error = null;
    notifyListeners();
  }

  Future<void> refresh() async {
    final a = api, id = deviceId;
    if (a == null || id == null) return;
    try {
      _set(RemoteAccess.fromJson(await a.get('/devices/$id/remote-access')));
    } catch (e) {
      debugPrint('[RemoteAccess] refresh failed: $e');
    }
  }

  Future<void> setInstall(bool allow) => _save({'allow_remote_install': allow});
  Future<void> setInvoke(bool allow) => _save({'allow_remote_invoke': allow});

  Future<void> _save(Map<String, dynamic> body) async {
    final a = api, id = deviceId;
    if (a == null || id == null) return;
    _saving = true;
    _error = null;
    notifyListeners();
    try {
      // The value that counts is the one the backend returns, not the one we asked for.
      _set(RemoteAccess.fromJson(await a.put('/devices/$id/remote-access', body)));
    } catch (e) {
      _error = "Couldn't save the setting. Check your connection and try again.";
      debugPrint('[RemoteAccess] save failed: $e');
    } finally {
      _saving = false;
      notifyListeners();
    }
  }

  void _set(RemoteAccess v) {
    _known = true;
    if (v != _value) _value = v;
    notifyListeners();
  }
}
