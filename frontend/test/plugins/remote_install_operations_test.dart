import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/plugins/capability_dispatcher.dart';
import 'package:pai_mobile/plugins/models/plugin_operation.dart';
import 'package:pai_mobile/plugins/plugin_manager.dart';
import 'package:pai_mobile/plugins/plugin_service.dart';

const _me = 'dev-me';
const _other = 'dev-other';

class _Api extends ApiClient {
  _Api() : super(baseUrl: 'http://test', apiPrefix: '/api/v1');
  final posts = <String>[];
  final bodies = <String, Map<String, dynamic>>{};
  final gets = <String>[];
  Object? installError;
  Map<String, dynamic> installReply = {};
  Map<String, dynamic> operationsReply = {'operations': []};

  @override
  Future<dynamic> get(String path, {Map<String, String>? query}) async {
    gets.add(path);
    if (path == '/plugins/catalog') {
      return {
        _me: [_info()],
        _other: [_info()],
      };
    }
    if (path == '/plugins/updates') return {'devices': {}};
    if (path == '/plugins/operations') return operationsReply;
    return {};
  }

  @override
  Future<dynamic> post(String path, Map<String, dynamic> body, {Map<String, String>? headers}) async {
    posts.add(path);
    bodies[path] = body;
    if (path.endsWith('/install') && installError != null) throw installError!;
    if (path.endsWith('/install')) return installReply;
    return {'ok': true};
  }
}

Map<String, dynamic> _info() => {
      'id': 'camera',
      'name': 'Camera',
      'version': '1.0.0',
      'type': 'native',
      'runtimeKind': 'device',
      'runtimeSpec': {'kind': 'device', 'nativeModule': 'pai.camera'},
      'capabilities': ['camera.capture'],
    };

class _Service implements PluginService {
  @override
  Future<List<Map<String, dynamic>>> localSnapshot() async => [];

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _Dispatcher implements CapabilityDispatcher {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Map<String, dynamic> _op(String status, {String id = 'op1', Map<String, dynamic>? error, String device = _other}) => {
      'id': id,
      'plugin_id': 'camera',
      'device_id': device,
      'version': '1.0.0',
      'kind': 'install',
      'status': status,
      'terminal': status == 'succeeded' || status == 'failed',
      'error': error,
    };

Future<(PluginManager, _Api)> _manager() async {
  final api = _Api();
  final m = PluginManager(service: _Service(), api: api, deviceId: _me, dispatcher: _Dispatcher())..stageHold = Duration.zero;
  await m.loadCatalog();
  api.posts.clear();
  api.gets.clear();
  return (m, api);
}

void main() {
  test('fast stages each stay on screen for the hold time, in order', () async {
    final (m, _) = await _manager();
    m.stageHold = const Duration(milliseconds: 60);
    for (final st in ['downloading', 'verifying', 'installing']) {
      m.handleOperationFrame({'type': 'plugin.operation', 'operation': _op(st)});
    }
    // all three arrived at once: only the first is shown
    expect(m.remoteStateFor(_other, 'camera').stageLabel, isNot(contains('Verif')));
    final labels = <String>{m.remoteStateFor(_other, 'camera').stageLabel};
    for (var i = 0; i < 10; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 40));
      labels.add(m.remoteStateFor(_other, 'camera').stageLabel);
    }
    expect(labels.length, 3);
    m.dispose();
  });

  test('requesting an install shows the stages the device reports, live, with no polling', () async {
    final (m, api) = await _manager();
    api.installReply = {'operation_id': 'op1', 'operation': _op('delivered')};

    await m.installOnDevice('camera', '1.0.0', _other);
    expect(m.remoteStateFor(_other, 'camera').phase, RemotePhase.installing);
    expect(m.remoteStateFor(_other, 'camera').stageLabel, 'Waiting for the device…');

    final seen = <String>[];
    for (final st in ['downloading', 'verifying', 'installing', 'awaiting_permission']) {
      m.handleOperationFrame({'type': 'plugin.operation', 'operation': _op(st)});
      seen.add(m.remoteStateFor(_other, 'camera').stageLabel);
    }
    expect(seen, ['Downloading…', 'Verifying…', 'Installing…', 'Waiting for permission on the device']);

    // Nothing polled the backend for state: no "installed" lookups and no timers.
    expect(api.gets.where((g) => g.contains('/installed')), isEmpty);
  });

  test('success is final: the server is told to enable it once and the catalog refreshes once', () async {
    final (m, api) = await _manager();
    m.handleOperationFrame({'type': 'plugin.operation', 'operation': _op('installing')});
    m.handleOperationFrame({'type': 'plugin.operation', 'operation': _op('succeeded')});
    m.handleOperationFrame({'type': 'plugin.operation', 'operation': _op('succeeded')}); // repeated after a reconnect
    await Future<void>.delayed(Duration.zero);
    await Future<void>.delayed(Duration.zero);

    expect(m.remoteStateFor(_other, 'camera').phase, RemotePhase.succeeded);
    expect(api.posts.where((p) => p == '/plugins/camera/enable'), hasLength(1));
    expect(api.gets.where((g) => g == '/plugins/catalog'), hasLength(1));
  });

  test('a device failure shows the backend message and hint, not a raw error', () async {
    final (m, _) = await _manager();
    m.handleOperationFrame({
      'type': 'plugin.operation',
      'operation': _op('failed', error: {
        'code': 'sha_mismatch',
        'message': "The downloaded plugin package didn't match its checksum, so it was not installed.",
        'hint': 'Try again.',
      }),
    });
    final s = m.remoteStateFor(_other, 'camera');
    expect(s.phase, RemotePhase.failed);
    expect(s.error, contains("didn't match its checksum"));
    expect(s.hint, 'Try again.');
    // a failed operation never changes back
    m.handleOperationFrame({'type': 'plugin.operation', 'operation': _op('installing')});
    expect(m.remoteStateFor(_other, 'camera').phase, RemotePhase.failed);
  });

  test('"remote install is off on that device" is explained, with what to do', () async {
    final (m, api) = await _manager();
    api.installError = ApiException(403, {
      'detail': {'code': 'remote_install_disabled', 'message': "Remote plugin installation is turned off on 'Pixel'."}
    });
    await expectLater(m.installOnDevice('camera', '1.0.0', _other), throwsA(isA<ApiException>()));
    final s = m.remoteStateFor(_other, 'camera');
    expect(s.phase, RemotePhase.failed);
    expect(s.error, contains('turned off'));
    expect(s.error, isNot(contains('ApiException')));
    expect(s.hint, contains('Allow remote plugin install'));
  });

  test('a network failure is plain words too', () async {
    final (m, api) = await _manager();
    api.installError = StateError('SocketException: connection refused');
    await expectLater(m.installOnDevice('camera', '1.0.0', _other), throwsA(isA<StateError>()));
    expect(m.remoteStateFor(_other, 'camera').error, isNot(contains('Socket')));
    expect(m.remoteStateFor(_other, 'camera').error, contains('connection'));
  });

  test('reconnect: operations still running are picked up from the backend', () async {
    final (m, api) = await _manager();
    api.operationsReply = {
      'operations': [_op('downloading')]
    };
    await m.loadActiveOperations();
    expect(m.remoteStateFor(_other, 'camera').stage, 'downloading');
  });

  test('an operation about THIS device is not shown as a remote install', () async {
    final (m, _) = await _manager();
    m.handleOperationFrame({'type': 'plugin.operation', 'operation': _op('installing', device: _me)});
    expect(m.remoteStateFor(_me, 'camera').phase, RemotePhase.idle);
  });

  test('malformed frames are ignored', () async {
    final (m, _) = await _manager();
    m.handleOperationFrame({'type': 'plugin.operation', 'operation': 'nope'});
    m.handleOperationFrame({'type': 'plugin.operation'});
    expect(m.remoteStateFor(_other, 'camera').phase, RemotePhase.idle);
  });

  test('the install result this device reports carries the operation id', () async {
    final (m, api) = await _manager();
    await m.reportCommandInstall(pluginId: 'camera', version: '1.0.0', operationId: 'op9', success: true);
    expect(api.bodies['/plugins/camera/report-install'], {
      'device_id': _me, 'operation_id': 'op9', 'version': '1.0.0', 'success': true,
    });
    await m.reportCommandUninstall(pluginId: 'camera', operationId: 'op10', success: false, error: 'x');
    expect(api.bodies['/plugins/camera/report-uninstall']!['operation_id'], 'op10');
  });

  test('stage labels read well', () {
    expect(remoteStageLabel('requested'), 'Waiting for the device…');
    expect(remoteStageLabel(null), 'Installing…');
  });
}
