import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/ui/plugins/permissions/permissions_screen.dart';

import '../ui/golden_harness.dart';
import 'fakes.dart';

Map<String, dynamic> grant(
  String id, {
  String scope = 'always',
  String plugin = 'camera',
  String cap = 'camera.capture',
  String device = 'pixel-8',
  Map<String, dynamic>? binding,
  String? preview,
  String? expires = '2027-02-21T12:00:00',
  String? lastUsed,
  int uses = 0,
  String risk = 'medium',
}) =>
    {
      'id': id,
      'scope': scope,
      'plugin_id': plugin,
      'capability': cap,
      'device_id': device,
      'binding': binding ?? {'mode': 'exact', 'keys': []},
      'params_preview': preview,
      'expires_at': expires,
      'last_used_at': lastUsed,
      'use_count': uses,
      'risk': risk,
    };

final sample = [
  grant('g1', binding: {'mode': 'exact', 'keys': []}, preview: 'camera=front', uses: 3, lastUsed: '2026-11-23T09:00:00'),
  grant('g2', scope: 'session', expires: null, binding: {'mode': 'any', 'keys': []}, device: 'laptop'),
  grant('g3', cap: 'camera.record', binding: {'mode': 'keys', 'keys': ['camera', 'flash']}, uses: 1, lastUsed: '2026-11-20T09:00:00'),
  grant('g4', plugin: 'shell', cap: 'shell.run', device: 'laptop', risk: 'high', preview: 'cmd=ls', expires: '2027-01-01T00:00:00'),
];

Future<FakeApi> open(WidgetTester t, {List<Map<String, dynamic>>? grants, Object? failWith, double textScale = 1.0}) async {
  final api = FakeApi();
  api.routes['GET /consents'] = (_) {
    if (failWith != null) throw failWith;
    return {'consents': grants ?? sample};
  };
  await pumpHud(
    t,
    PermissionsScreen(service: fastService(api), deviceNames: const {'pixel-8': 'Pixel 8', 'laptop': 'ThinkPad'}),
    textScale: textScale,
  );
  await t.pump();
  await t.pump(const Duration(milliseconds: 50));
  return api;
}

void main() {
  setUpAll(loadPaiFonts);

  testWidgets('groups by plugin then capability and shows chips, bindings, expiry and usage', (t) async {
    await open(t);
    expect(find.text('CAMERA'), findsOneWidget); // HUD section headers are upper-case
    expect(find.text('SHELL'), findsOneWidget);
    expect(find.text('camera.capture'), findsOneWidget);
    expect(find.text('camera.record'), findsOneWidget);
    expect(find.text('Pixel 8'), findsWidgets);
    expect(find.text('ThinkPad'), findsWidgets);

    expect(t.widget<Text>(find.byKey(const Key('binding-g1'))).data, 'exact params: camera=front');
    expect(t.widget<Text>(find.byKey(const Key('binding-g2'))).data, 'any params');
    expect(t.widget<Text>(find.byKey(const Key('binding-g3'))).data, 'keys: camera, flash');
    expect(t.widget<Text>(find.byKey(const Key('expiry-g2'))).data, 'ends with the session');
    expect(t.widget<Text>(find.byKey(const Key('expiry-g1'))).data, startsWith('expires '));
    expect(t.widget<Text>(find.byKey(const Key('usage-g2'))).data, 'never used');
    expect(t.widget<Text>(find.byKey(const Key('usage-g1'))).data, contains('used 3 times'));
    expect(t.widget<Text>(find.byKey(const Key('usage-g3'))).data, contains('used once'));
    expect(find.text('high risk'), findsOneWidget);
  });

  testWidgets('empty state explains where permissions come from', (t) async {
    await open(t, grants: const []);
    expect(find.text('NO SAVED PERMISSIONS'), findsOneWidget);
  });

  testWidgets('load error shows retry that reloads', (t) async {
    final logs = await captureLogs(() async {
      final api = await open(t, failWith: Unreachable());
      expect(find.text('COULD NOT LOAD PERMISSIONS'), findsOneWidget);
      api.routes['GET /consents'] = (_) => {'consents': sample};
      await t.tap(find.text('RETRY'));
      await t.pump();
      await t.pump(const Duration(milliseconds: 50));
      expect(find.text('COULD NOT LOAD PERMISSIONS'), findsNothing);
      expect(find.byKey(const Key('grant-g1')), findsOneWidget);
    });
    expect(logs.join('\n'), contains('[PermissionsScreen] load failed'));
  });

  testWidgets('revoke: confirm dialog defaults to "Keep it"; cancelling does nothing', (t) async {
    final api = await open(t);
    await t.tap(find.byKey(const Key('revoke-g1')));
    await t.pump(const Duration(milliseconds: 250));
    expect(t.widget<TextButton>(find.byKey(const Key('revoke-cancel'))).autofocus, isTrue);
    await t.tap(find.byKey(const Key('revoke-cancel')));
    await t.pumpAndSettle();
    expect(api.where('DELETE', '/consents'), isEmpty);
    expect(find.byKey(const Key('grant-g1')), findsOneWidget);
  });

  testWidgets('revoke: removed immediately and DELETE sent', (t) async {
    final api = await open(t);
    api.routes['DELETE /consents/g1'] = (_) => {'ok': true};
    await t.tap(find.byKey(const Key('revoke-g1')));
    await t.pump(const Duration(milliseconds: 250));
    await t.tap(find.byKey(const Key('revoke-confirm')));
    await t.pumpAndSettle();
    expect(find.byKey(const Key('grant-g1')), findsNothing);
    expect(find.byKey(const Key('grant-g2')), findsOneWidget);
    expect(api.where('DELETE', '/consents/g1'), hasLength(1));
  });

  testWidgets('revoke failure restores the entry and says why', (t) async {
    await captureLogs(() async {
      final api = await open(t);
      api.routes['DELETE /consents/g2'] = (_) => throw Unreachable();
      await t.ensureVisible(find.byKey(const Key('revoke-g2')));
      await t.tap(find.byKey(const Key('revoke-g2')));
      await t.pump(const Duration(milliseconds: 250));
      await t.tap(find.byKey(const Key('revoke-confirm')));
      await t.pump();
      await t.pump(const Duration(milliseconds: 100));
      expect(find.byKey(const Key('grant-g2')), findsOneWidget);
      expect(find.textContaining('Could not revoke camera.capture'), findsOneWidget);
    });
  });

  testWidgets('an unknown scope from the server is shown as the broader "always"', (t) async {
    await open(t, grants: [grant('x', scope: 'forever')]);
    expect(find.byKey(const Key('grant-x')), findsOneWidget);
    expect(find.textContaining(RegExp('always', caseSensitive: false), findRichText: true), findsWidgets);
  });

  testWidgets('text scale 1.3 does not overflow', (t) async {
    await open(t, textScale: 1.3);
    expect(t.takeException(), isNull);
  });

  testWidgets('golden: permissions list', (t) async {
    await open(t);
    await expectGolden(t, 'permissions_list');
  });
}
