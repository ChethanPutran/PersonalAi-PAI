import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/plugins/capability_dispatcher.dart';
import 'package:pai_mobile/plugins/models/permission_consent_handler.dart';
import 'package:pai_mobile/plugins/models/plugin_info.dart';
import 'package:pai_mobile/plugins/permission_reporter.dart';
import 'package:pai_mobile/plugins/plugin_manager.dart';
import 'package:pai_mobile/plugins/plugin_service.dart';

/// The device tells the backend what the user allowed here, so the server stops offering capabilities this
/// device would refuse. Advisory: unknown (platform can't say) must report NOTHING, never "granted"/"denied".

class FakeApi extends ApiClient {
  FakeApi() : super(baseUrl: 'http://test', apiPrefix: '/api/v1');
  final sent = <Map<String, dynamic>>[];
  final paths = <String>[];
  bool failPermissions = false;

  @override
  Future<dynamic> get(String path, {Map<String, String>? query}) async {
    if (path == '/plugins/catalog') {
      return {
        'dev-1': [_info('input', ['input']), _info('notes', [])]
      };
    }
    if (path == '/plugins/updates') return {'devices': {}};
    return {};
  }

  @override
  Future<dynamic> post(String path, Map<String, dynamic> body, {Map<String, String>? headers}) async {
    paths.add(path);
    if (path.endsWith('/permissions')) {
      if (failPermissions) throw StateError('boom');
      sent.add(body);
    }
    return {'ok': true};
  }
}

Map<String, dynamic> _info(String id, List<String> perms) => {
      'id': id,
      'name': id,
      'version': '1.0.0',
      'type': 'native',
      'runtimeKind': 'device',
      'runtimeSpec': {'kind': 'device', 'nativeModule': 'pai.$id'},
      'permissions': perms,
      'capabilities': ['$id.do'],
    };

class FakeService implements PluginService {
  final _installed = <String>{};
  @override
  Future<PluginInfo> install(String id, String version, {PermissionConsentHandler? onPermissionConsent}) async {
    _installed.add(id);
    return PluginInfo.fromJson(_info(id, id == 'input' ? ['input'] : []));
  }

  @override
  Future<List<Map<String, dynamic>>> localSnapshot() async =>
      [for (final id in _installed) {'id': id, 'version': '1.0.0', 'enabled': true}];
  @override
  Future<bool> enable(String id, {PermissionConsentHandler? onPermissionConsent}) async => true;
  @override
  Future<void> disable(String id) async {}
  @override
  Future<void> uninstall(String id) async => _installed.remove(id);
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class FakeDispatcher implements CapabilityDispatcher {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  group('PermissionReporter', () {
    test('reports each plugin only for the permissions it declares, with the device answers', () async {
      final api = FakeApi();
      final asked = <String>[];
      final r = PermissionReporter(
          api: api,
          deviceId: 'dev-1',
          status: (module, p) async {
            asked.add('$module:${p.join(',')}');
            return {'input': 'denied', 'screen': 'granted', 'camera': 'pending'};
          });
      final ok = await r.report({'input': ['input'], 'shot': ['screen', 'camera']},
          nativeModules: {'input': 'pai.input', 'shot': 'pai.shot'});
      expect(ok, isTrue);
      // One query PER plugin: a decision made for one plugin is never read as another's.
      expect(asked, ['pai.input:input', 'pai.shot:screen,camera']);
      expect(api.paths.single, '/plugins/device/dev-1/permissions');
      expect(api.sent.single, {
        'plugins': {
          'input': {'input': 'denied'},
          'shot': {'screen': 'granted', 'camera': 'pending'},
        }
      });
    });

    test('a platform that cannot answer reports nothing at all (unknown is not granted/denied)', () async {
      final api = FakeApi();
      final r = PermissionReporter(api: api, deviceId: 'dev-1', status: (_, __) async => null);
      expect(await r.report({'input': ['input']}), isFalse);
      expect(api.paths, isEmpty);
    });

    test('permissions the device did not answer, or answered oddly, are left out', () async {
      final api = FakeApi();
      final r = PermissionReporter(
          api: api, deviceId: 'dev-1', status: (_, __) async => {'input': 'maybe', 'screen': 'denied'});
      await r.report({'a': ['input'], 'b': ['screen', 'camera']});
      expect(api.sent.single, {'plugins': {'b': {'screen': 'denied'}}});
    });

    test('unchanged reports are not re-sent; a change, force and reset are', () async {
      final api = FakeApi();
      var state = 'denied';
      final r = PermissionReporter(api: api, deviceId: 'dev-1', status: (_, __) async => {'input': state});
      final d = {'input': ['input']};
      expect(await r.report(d), isTrue);
      expect(await r.report(d), isFalse);
      state = 'granted';
      expect(await r.report(d), isTrue);                        // the user changed their mind
      expect(await r.report(d, force: true), isTrue);
      r.reset();
      expect(await r.report(d), isTrue);
      expect(api.sent.length, 4);
    });

    test('a failed send is not remembered as sent (retried next time) and never throws', () async {
      final api = FakeApi()..failPermissions = true;
      final r = PermissionReporter(api: api, deviceId: 'dev-1', status: (_, __) async => {'input': 'denied'});
      expect(await r.report({'input': ['input']}), isFalse);
      api.failPermissions = false;
      expect(await r.report({'input': ['input']}), isTrue);
    });

    test('the same permission is answered separately for each plugin', () async {
      final api = FakeApi();
      final r = PermissionReporter(
          api: api,
          deviceId: 'dev-1',
          status: (module, p) async => {'filesystem': module == 'pai.camera' ? 'granted' : 'pending'});
      await r.report({'camera_linux': ['filesystem'], 'files': ['filesystem']},
          nativeModules: {'camera_linux': 'pai.camera', 'files': 'pai.files'});
      expect(api.sent.single, {
        'plugins': {
          'camera_linux': {'filesystem': 'granted'},
          'files': {'filesystem': 'pending'},       // allowing the camera never allowed the files plugin
        }
      });
    });

    test('one plugin the platform cannot answer for does not hide the others', () async {
      final api = FakeApi();
      final r = PermissionReporter(
          api: api, deviceId: 'dev-1', status: (module, _) async => module == 'a' ? null : {'input': 'granted'});
      await r.report({'x': ['input'], 'y': ['input']}, nativeModules: {'x': 'a', 'y': 'b'});
      expect(api.sent.single, {'plugins': {'y': {'input': 'granted'}}});
    });

    test('plugins declaring no permissions cause no call', () async {
      final api = FakeApi();
      final r = PermissionReporter(api: api, deviceId: 'dev-1', status: (_, __) async => {});
      expect(await r.report({'notes': []}), isFalse);
      expect(api.paths, isEmpty);
    });
  });

  group('PluginManager reports after changes', () {
    Future<(PluginManager, FakeApi)> make({bool withReporter = true, Map<String, String>? answers}) async {
      final api = FakeApi();
      final m = PluginManager(
        service: FakeService(),
        api: api,
        deviceId: 'dev-1',
        dispatcher: FakeDispatcher(),
        permissionReporter: withReporter
            ? PermissionReporter(
                api: api, deviceId: 'dev-1', status: (_, __) async => answers ?? {'input': 'denied'})
            : null,
      );
      await m.loadCatalog();
      api.paths.clear();
      return (m, api);
    }

    test('install, disable and enable each report the device decision', () async {
      final (m, api) = await make();
      await m.install('input', '1.0.0');
      expect(api.sent.length, 1);
      expect(api.sent.last, {'plugins': {'input': {'input': 'denied'}}});
      await m.disable('input');
      await m.enable('input');
      expect(api.sent.length, 1);                              // unchanged -> deduped, not spammed
    });

    test('a plugin without declared permissions is never reported; nothing without a reporter', () async {
      final (m, api) = await make(withReporter: false);
      await m.install('input', '1.0.0');
      expect(api.sent, isEmpty);
      final (m2, api2) = await make();
      await m2.install('notes', '1.0.0');
      expect(api2.sent, isEmpty);
    });

    test('a reconcile forces a fresh report (the server may have restarted)', () async {
      final (m, api) = await make();
      await m.install('input', '1.0.0');
      expect(api.sent.length, 1);
      await m.reconcileWithBackend();
      expect(api.sent.length, 2);
    });

    test('a reporter failure never breaks install', () async {
      final (m, api) = await make();
      api.failPermissions = true;
      await m.install('input', '1.0.0');
      expect(m.isInstalled('input'), isTrue);
    });
  });
}
