import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/plugins/builtin_plugins.dart';

class _Api extends ApiClient {
  _Api() : super(baseUrl: 'http://test', apiPrefix: '/api/v1');

  var agentEnabled = false;
  Object? failPost;
  Object? failGet;
  final posts = <String>[];
  final bodies = <Map<String, dynamic>>[];

  @override
  Future<dynamic> get(String path, {Map<String, String>? query}) async {
    if (failGet != null) throw failGet!;
    return {
      'plugins': [
        {'id': 'camera_android', 'name': 'Camera', 'description': 'photos', 'version': '1.0.0', 'builtin': false, 'enabled': true},
        {
          'id': 'agent',
          'name': 'Agent',
          'description': 'backend description',
          'version': '1.0.0',
          'builtin': true,
          'enabled': agentEnabled,
        },
      ],
    };
  }

  @override
  Future<dynamic> post(String path, Map<String, dynamic> body, {Map<String, String>? headers}) async {
    posts.add(path);
    bodies.add(body);
    if (failPost != null) throw failPost!;
    agentEnabled = path.endsWith('/enable');
    return {'plugin_id': 'agent', 'status': agentEnabled ? 'enabled' : 'disabled'};
  }
}

void main() {
  test('load keeps only the built-in plugins (not the per-device ones)', () async {
    final c = BuiltinPluginsController(api: _Api());
    expect(c.loaded, isFalse);
    await c.load();
    expect(c.loaded, isTrue);
    expect(c.plugins.map((p) => p.id), ['agent']);
    expect(c.plugins.single.enabled, isFalse);
  });

  test('turning it on moves the switch at once, then POSTs enable with an empty body', () async {
    final api = _Api();
    final c = BuiltinPluginsController(api: api);
    await c.load();
    final done = c.setEnabled('agent', true);
    expect(c.plugins.single.enabled, isTrue); // optimistic: before the server answered
    expect(c.busy, contains('agent'));
    await done;
    expect(api.posts, ['/plugins/agent/enable']);
    expect(api.bodies.single, isEmpty);
    expect(c.plugins.single.enabled, isTrue);
    expect(c.busy, isEmpty);

    await c.setEnabled('agent', false);
    expect(api.posts.last, '/plugins/agent/disable');
    expect(c.plugins.single.enabled, isFalse);
  });

  test('a refused change rolls the switch back and says why in plain words', () async {
    final api = _Api()..failPost = ApiException(403, {'detail': 'Not allowed.'});
    final c = BuiltinPluginsController(api: api);
    await c.load();
    await c.setEnabled('agent', true);
    expect(c.plugins.single.enabled, isFalse);
    expect(c.error, contains('Could not turn Agent on'));
    expect(c.error, contains('Not allowed.'));
    expect(c.busy, isEmpty);
  });

  test('a network failure also rolls back, with a generic hint', () async {
    final api = _Api()..failPost = Exception('socket');
    final c = BuiltinPluginsController(api: api);
    await c.load();
    await c.setEnabled('agent', true);
    expect(c.plugins.single.enabled, isFalse);
    expect(c.error, contains('Check your connection'));
  });

  test('a failed load shows nothing and a failed refresh keeps the working list', () async {
    final api = _Api()..failGet = Exception('offline');
    final c = BuiltinPluginsController(api: api);
    await c.load();
    expect(c.loaded, isFalse); // the UI renders nothing for it
    api.failGet = null;
    await c.load();
    expect(c.loaded, isTrue);
    api.failGet = Exception('offline');
    await c.load();
    expect(c.plugins.single.id, 'agent'); // not blanked
  });
}
