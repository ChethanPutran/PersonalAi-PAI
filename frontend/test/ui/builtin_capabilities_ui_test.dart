import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/plugins/builtin_plugins.dart';
import 'package:pai_mobile/ui/plugins/plugins_screen.dart';
import 'package:pai_mobile/ui/plugins/widgets/builtin_capabilities_section.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import 'fakes.dart';
import 'golden_harness.dart';

class _Api extends ApiClient {
  _Api() : super(baseUrl: 'http://test', apiPrefix: '/api/v1');
  var enabled = false;
  var failPost = false;
  final posts = <String>[];

  @override
  Future<dynamic> get(String path, {Map<String, String>? query}) async => {
        'plugins': [
          {'id': 'agent', 'name': 'Agent', 'description': 'backend text', 'version': '1.0.0', 'builtin': true, 'enabled': enabled},
        ],
      };

  @override
  Future<dynamic> post(String path, Map<String, dynamic> body, {Map<String, String>? headers}) async {
    posts.add(path);
    if (failPost) throw ApiException(500, {'detail': 'boom'});
    enabled = path.endsWith('/enable');
    return {};
  }
}

Future<void> _settle(WidgetTester t) async {
  for (var i = 0; i < 6; i++) {
    await t.pump(const Duration(milliseconds: 60));
  }
}

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  Future<(BuiltinPluginsController, _Api)> pumpScreen(WidgetTester t) async {
    final api = _Api();
    final c = BuiltinPluginsController(api: api);
    await pumpHud(
      t,
      PluginsScreen(
        manager: FakePluginManager(),
        deviceRepository: FakeDeviceRepository(const []),
        thisDevice: null,
        builtinPlugins: c,
      ),
      size: const Size(400, 1400),
    );
    await _settle(t);
    return (c, api);
  }

  testWidgets('the Plugins screen shows a "Built-in capabilities" section ABOVE the device plugins', (t) async {
    await pumpScreen(t);
    expect(find.text('BUILT-IN CAPABILITIES'), findsOneWidget);
    expect(find.text('Agent'), findsOneWidget);
    expect(find.text(BuiltinCapabilitiesSection.agentExplanation), findsOneWidget);
    expect(find.byKey(const Key('builtin-switch-agent')), findsOneWidget);
    final section = t.getTopLeft(find.byKey(const Key('builtin-capabilities'))).dy;
    final firstPlugin = t.getTopLeft(find.text('Camera').first).dy;
    expect(section, lessThan(firstPlugin));
  });

  testWidgets('the switch turns the agent on and shows the computer-control hint', (t) async {
    final (c, api) = await pumpScreen(t);
    expect(find.byKey(const Key('agent-computer-control-hint')), findsNothing);
    await t.tap(find.byKey(const Key('builtin-switch-agent')));
    await _settle(t);
    expect(api.posts, ['/plugins/agent/enable']);
    expect(c.plugins.single.enabled, isTrue);
    expect(find.byKey(const Key('agent-computer-control-hint')), findsOneWidget);
    expect(find.textContaining('Computer control plugin'), findsOneWidget);
  });

  testWidgets('a failure puts the switch back and shows a plain message', (t) async {
    final (c, api) = await pumpScreen(t);
    api.failPost = true;
    await t.tap(find.byKey(const Key('builtin-switch-agent')));
    await _settle(t);
    expect(c.plugins.single.enabled, isFalse);
    expect(find.byKey(const Key('builtin-error')), findsOneWidget);
    expect(find.textContaining('Could not turn Agent on'), findsOneWidget);
    expect(t.widget<Switch>(find.byKey(const Key('builtin-switch-agent'))).value, isFalse);
  });

  testWidgets('without a controller the screen has no section (nothing changes for other callers)', (t) async {
    await pumpHud(
      t,
      PluginsScreen(manager: FakePluginManager(), deviceRepository: FakeDeviceRepository(const []), thisDevice: null),
      size: const Size(400, 900),
    );
    await _settle(t);
    expect(find.byKey(const Key('builtin-capabilities')), findsNothing);
  });
}
