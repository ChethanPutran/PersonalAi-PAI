import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/models/device.dart';
import 'package:pai_mobile/plugins/capability_dispatcher.dart';
import 'package:pai_mobile/plugins/models/plugin_capability.dart';
import 'package:pai_mobile/plugins/permissions/permission_models.dart';
import 'package:pai_mobile/plugins/permissions/permissions_service.dart';
import 'package:pai_mobile/plugins/plugin_manager.dart';

class Call {
  Call(this.method, this.path, this.body);
  final String method;
  final String path;
  final Map<String, dynamic>? body;
  @override
  String toString() => '$method $path ${body ?? ''}';
}

/// ApiClient without a network. Routes are keyed "METHOD /path"; a handler may return data,
/// or throw (ApiException, SocketException-like, ...). Every call is recorded.
class FakeApi extends ApiClient {
  FakeApi() : super(baseUrl: 'http://backend.test', apiPrefix: '/api/v1');

  final List<Call> calls = [];
  final Map<String, FutureOr<dynamic> Function(Map<String, dynamic>? body)> routes = {};

  List<Call> where(String method, String pathStart) =>
      [for (final c in calls) if (c.method == method && c.path.startsWith(pathStart)) c];

  Future<dynamic> _dispatch(String method, String path, Map<String, dynamic>? body) async {
    calls.add(Call(method, path, body));
    final h = routes['$method $path'];
    if (h == null) throw ApiException(404, {'detail': 'no fake route for $method $path'});
    return h(body);
  }

  @override
  Future<dynamic> get(String path, {Map<String, String>? query}) => _dispatch('GET', path, null);

  @override
  Future<dynamic> post(String path, Map<String, dynamic> body, {Map<String, String>? headers}) =>
      _dispatch('POST', path, body);

  @override
  Future<dynamic> delete(String path) => _dispatch('DELETE', path, null);
}

/// A transport-level failure (what the http package throws when the server is unreachable).
class Unreachable implements Exception {
  @override
  String toString() => 'Connection refused';
}

/// PermissionsService with instant waits so polling tests don't sleep.
PermissionsService fastService(FakeApi api, {DateTime Function()? clock}) => PermissionsService(
      api,
      sessionId: 'app-test',
      clock: clock,
      delay: (_) async {},
    );

Map<String, dynamic> confirmationJson({
  String capability = 'camera.capture',
  String risk = 'medium',
  List<String> scopes = const ['once', 'session', 'always'],
  bool canBind = true,
  List<Map<String, String>>? params,
  String? reason,
  String? deviceId = 'pixel-8',
}) =>
    {
      'capability': capability,
      'plugin_id': 'camera',
      'device_id': deviceId,
      'risk': risk,
      'parameters_summary': params ??
          [
            {'name': 'camera', 'value_preview': 'front'},
            {'name': 'flash', 'value_preview': 'false'},
          ],
      'allowed_scopes': scopes,
      'can_bind_params': canBind,
      'default_binding': {'mode': 'exact', 'keys': []},
      'reason': reason,
    };

ConfirmationRequest sampleRequest({
  String risk = 'medium',
  List<String> scopes = const ['once', 'session', 'always'],
  bool canBind = true,
  List<Map<String, String>>? params,
  String? reason = 'Takes a photo with the front camera.',
}) =>
    ConfirmationRequest.fromJson(confirmationJson(
        risk: risk, scopes: scopes, canBind: canBind, params: params, reason: reason))
        .withDeviceName('Pixel 8');

// ---- plugin tester fakes ----------------------------------------------------------------

class FakeTesterManager implements PluginManager {
  FakeTesterManager(this.apiClient);
  final ApiClient apiClient;
  @override
  ApiClient get api => apiClient;
  @override
  bool isInstalled(String pluginId) => true;
  @override
  bool isEnabled(String pluginId) => true;
  @override
  bool isInstalledOnDevice(String pluginId, String deviceId) => true;
  @override
  bool isEnabledOnDevice(String pluginId, String deviceId) => true;
  @override
  Future<void> loadCatalog({Duration? maxAge}) async {}
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class FakeDispatcher implements CapabilityDispatcher {
  final List<Map<String, dynamic>> invoked = [];
  Map<String, dynamic> response = {'path': '/sdcard/photo.jpg'};
  @override
  Future<Map<String, dynamic>> invoke({
    required String capability,
    required Map<String, dynamic> parameters,
  }) async {
    invoked.add({'capability': capability, 'parameters': parameters});
    return response;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

const capability = PluginCapability(
  id: 'camera.capture',
  description: 'Take a photo',
  requireConfirmation: true,
  parameters: {
    'type': 'object',
    'properties': {
      'camera': {'type': 'string', 'default': 'front'},
    },
  },
);

/// Collects debugPrint output while [body] runs.
Future<List<String>> captureLogs(Future<void> Function() body) async {
  final logs = <String>[];
  final original = debugPrint;
  debugPrint = (String? m, {int? wrapWidth}) {
    if (m != null) logs.add(m);
  };
  try {
    await body();
  } finally {
    debugPrint = original;
  }
  return logs;
}

DeviceInfo? deviceFor(DeviceInfo Function() make) => make();
