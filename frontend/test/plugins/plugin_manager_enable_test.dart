import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/plugins/capability_dispatcher.dart';
import 'package:pai_mobile/plugins/models/permission_consent_handler.dart';
import 'package:pai_mobile/plugins/models/plugin_info.dart';
import 'package:pai_mobile/plugins/plugin_manager.dart';
import 'package:pai_mobile/plugins/plugin_service.dart';

/// Regression: installing a plugin made the app SHOW it as enabled, but nothing told the server, so every
/// action failed with "'keyboard.type' is provided by ['input'] but none are enabled for your account".

const _device = 'dev-1';
const _other = 'dev-2';

class FakeApi extends ApiClient {
  FakeApi() : super(baseUrl: 'http://test', apiPrefix: '/api/v1');

  final posts = <String>[];
  final bodies = <String, Map<String, dynamic>>{};
  bool failEnable = false;

  @override
  Future<dynamic> get(String path, {Map<String, String>? query}) async {
    if (path == '/plugins/catalog') {
      return {
        _device: [_info('input')],
        _other: [_info('input')],
      };
    }
    if (path == '/plugins/updates') return {'devices': {}};
    if (path == '/plugins/device/$_other/installed') {
      return {
        'plugins': [
          {'plugin_id': 'input', 'is_installed': true, 'version': '1.0.0'}
        ]
      };
    }
    return {};
  }

  @override
  Future<dynamic> post(String path, Map<String, dynamic> body, {Map<String, String>? headers}) async {
    posts.add(path);
    bodies[path] = body;
    if (failEnable && path.endsWith('/enable')) throw StateError('server said no');
    return {'ok': true};
  }
}

Map<String, dynamic> _info(String id) => {
      'id': id,
      'name': 'Input',
      'version': '1.0.0',
      'type': 'native',
      'runtimeKind': 'device',
      'runtimeSpec': {'kind': 'device', 'nativeModule': 'pai.input'},
      'capabilities': ['keyboard.type'],
    };

class FakeService implements PluginService {
  Object? installError;

  @override
  Future<PluginInfo> install(String id, String version, {PermissionConsentHandler? onPermissionConsent}) async {
    if (installError != null) throw installError!;
    return PluginInfo.fromJson(_info(id));
  }

  @override
  Future<List<Map<String, dynamic>>> localSnapshot() async => [];

  @override
  Future<bool> enable(String id, {PermissionConsentHandler? onPermissionConsent}) async => true;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class FakeDispatcher implements CapabilityDispatcher {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<(PluginManager, FakeApi, FakeService)> _manager() async {
  final api = FakeApi();
  final service = FakeService();
  final m = PluginManager(service: service, api: api, deviceId: _device, dispatcher: FakeDispatcher());
  await m.loadCatalog();
  api.posts.clear();
  return (m, api, service);
}

void main() {
  test('a successful install is reported AND enabled on the server, in that order', () async {
    final (m, api, _) = await _manager();
    await m.install('input', '1.0.0');
    expect(api.posts, ['/plugins/input/report-install', '/plugins/input/enable']);
    expect(api.bodies['/plugins/input/enable'], {'device_id': _device});
    expect(m.isEnabled('input'), isTrue);
    expect(m.errorFor('input'), isNull);
  });

  test('an install whose permission is still pending does NOT enable it on the server', () async {
    final (m, api, service) = await _manager();
    service.installError = PluginPermissionPendingException(
        id: 'input', nativeModule: 'pai.input', pending: ['input'], permissions: ['input']);
    await expectLater(m.install('input', '1.0.0'), throwsA(isA<PluginPermissionPendingException>()));
    expect(api.posts.where((p) => p.endsWith('/enable')), isEmpty);
    expect(m.isEnabled('input'), isFalse);
  });

  test('an install whose permission was denied does NOT enable it on the server', () async {
    final (m, api, service) = await _manager();
    service.installError =
        PluginPermissionDeniedException(id: 'input', denied: ['input'], permissions: ['input']);
    await expectLater(m.install('input', '1.0.0'), throwsA(isA<PluginPermissionDeniedException>()));
    expect(api.posts.where((p) => p.endsWith('/enable')), isEmpty);
  });

  test('if the server refuses the enable, the user is told how to retry (not left with a silent dead plugin)', () async {
    final (m, api, _) = await _manager();
    api.failEnable = true;
    await m.install('input', '1.0.0');                      // the install itself still succeeds
    expect(m.isInstalled('input'), isTrue);
    expect(m.errorFor('input'), allOf(contains('could not enable'), contains('Enable')));
  });

  test('the Enable switch still enables it on the server (once per toggle)', () async {
    final (m, api, _) = await _manager();
    await m.install('input', '1.0.0');
    api.posts.clear();
    await m.enable('input');
    expect(api.posts, ['/plugins/input/enable']);
  });

  test('a plugin installed on another device is enabled for that device once it reports installed', () async {
    final (m, api, _) = await _manager();
    await m.installOnDevice('input', '1.0.0', _other);
    // the poll runs asynchronously every 2s
    for (var i = 0; i < 40 && !api.posts.contains('/plugins/input/enable'); i++) {
      await Future<void>.delayed(const Duration(milliseconds: 150));
    }
    expect(api.posts, containsAllInOrder(['/plugins/input/install', '/plugins/input/enable']));
    expect(api.bodies['/plugins/input/enable'], {'device_id': _other});
  });
}
