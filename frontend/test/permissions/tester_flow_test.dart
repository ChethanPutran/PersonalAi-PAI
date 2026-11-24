import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/models/device.dart';
import 'package:pai_mobile/plugins/models/plugin_info.dart';
import 'package:pai_mobile/ui/plugins/plugin_test_screen.dart';

import '../ui/golden_harness.dart';
import 'fakes.dart';

const me = 'phone-1';

DeviceInfo device(String id, {bool connected = true, String name = 'Pixel 8'}) => DeviceInfo(
      id: id,
      name: name,
      appVersion: '1',
      runtimeVersion: '1',
      platform: 'android',
      osVersion: '14',
      architecture: 'arm64',
      deviceType: 'phone',
      status: connected ? 'online' : 'offline',
      connected: connected,
    );

const plugin = PluginInfo(
  id: 'camera',
  name: 'Camera',
  version: '1.0.0',
  type: PluginLanguage.kotlin,
  runtimeKind: RuntimeKind.device,
  runtimeSpec: PluginRuntimeSpec(kind: RuntimeKind.device, language: PluginLanguage.kotlin, nativeModule: 'cam'),
);

class Rig {
  Rig(this.api, this.dispatcher);
  final FakeApi api;
  final FakeDispatcher dispatcher;
}

Future<Rig> open(
  WidgetTester t, {
  required bool remote,
  bool admin = false,
  bool connected = true,
  void Function(FakeApi api)? routes,
}) async {
  final api = FakeApi();
  api.routes['GET /auth/me'] = (_) => {'is_admin': admin};
  routes?.call(api);
  final dispatcher = FakeDispatcher();
  await pumpHud(
    t,
    Scaffold(
      body: CapabilityTestSheet(
        manager: FakeTesterManager(api),
        dispatcher: dispatcher,
        plugin: plugin,
        capability: capability,
        thisDeviceId: me,
        device: device(remote ? 'laptop-1' : me, connected: connected, name: remote ? 'ThinkPad' : 'Pixel 8'),
        permissions: fastService(api),
      ),
    ),
    size: const Size(420, 900),
  );
  await t.pump(const Duration(milliseconds: 50));
  return Rig(api, dispatcher);
}

Future<void> invoke(WidgetTester t) async {
  final f = find.text('Invoke');
  await t.ensureVisible(f);
  await t.tap(f);
  await t.pump();
  await t.pump(const Duration(milliseconds: 300));
}

Map<String, dynamic> awaiting({String task = 't-1', List<String> scopes = const ['once', 'session', 'always']}) => {
      'task_id': task,
      'status': 'awaiting_confirmation',
      'awaiting_confirmation': true,
      'confirmation': confirmationJson(scopes: scopes, deviceId: 'laptop-1'),
    };

Map<String, dynamic> completed(Object output) => {
      'task': {'status': 'completed', 'result': {'success': true, 'output': output}},
    };

void main() {
  setUpAll(loadPaiFonts);

  group('remote device', () {
    testWidgets('admin: immediate result, no dialog', (t) async {
      final rig = await open(t, remote: true, admin: true, routes: (api) {
        api.routes['POST /devices/laptop-1/invoke'] =
            (_) => {'task_id': 't-9', 'status': 'completed', 'result': {'path': '/tmp/a.jpg'}};
      });
      await invoke(t);
      await t.pumpAndSettle();
      expect(find.byKey(const Key('confirm-title')), findsNothing);
      expect(find.textContaining('/tmp/a.jpg', findRichText: true), findsWidgets);
      final body = rig.api.where('POST', '/devices/laptop-1/invoke').single.body!;
      expect(body['capability'], 'camera.capture');
      expect(body['session_id'], 'app-test');
      expect(rig.dispatcher.invoked, isEmpty); // nothing ran natively
    });

    testWidgets('non-admin: 202 awaiting → dialog → Allow once → result', (t) async {
      final rig = await open(t, remote: true, routes: (api) {
        api.routes['POST /devices/laptop-1/invoke'] = (_) => awaiting();
        api.routes['POST /tasks/t-1/confirm'] = (_) => {'ok': true};
        api.routes['GET /tasks/t-1'] = (_) => completed({'path': '/tmp/b.jpg'});
      });
      await invoke(t);
      expect(find.byKey(const Key('confirm-title')), findsOneWidget);
      expect(find.text('ThinkPad'), findsWidgets);
      // nothing was approved without the user acting
      expect(rig.api.where('POST', '/tasks/t-1/confirm'), isEmpty);

      await t.tap(find.byKey(const Key('confirm-allow')));
      await t.pump(const Duration(milliseconds: 100));
      await t.pumpAndSettle();
      expect(rig.api.where('POST', '/tasks/t-1/confirm').single.body, {'scope': 'once'});
      expect(find.textContaining('/tmp/b.jpg', findRichText: true), findsWidgets);
    });

    testWidgets('non-admin picks "this session": body carries scope and binding', (t) async {
      final rig = await open(t, remote: true, routes: (api) {
        api.routes['POST /devices/laptop-1/invoke'] = (_) => awaiting();
        api.routes['POST /tasks/t-1/confirm'] = (_) => {'ok': true};
        api.routes['GET /tasks/t-1'] = (_) => completed({'ok': 1});
      });
      await invoke(t);
      await t.tap(find.byKey(const Key('scope-session')));
      await t.pump(const Duration(milliseconds: 100));
      await t.tap(find.byKey(const Key('confirm-allow')));
      await t.pump(const Duration(milliseconds: 100));
      await t.pumpAndSettle();
      expect(rig.api.where('POST', '/tasks/t-1/confirm').single.body, {
        'scope': 'session',
        'binding': {'mode': 'exact', 'keys': []},
      });
    });

    testWidgets('deny: server told, nothing runs, user sees "Denied"', (t) async {
      final rig = await open(t, remote: true, routes: (api) {
        api.routes['POST /devices/laptop-1/invoke'] = (_) => awaiting();
        api.routes['POST /tasks/t-1/deny'] = (_) => {'ok': true};
      });
      await invoke(t);
      await t.tap(find.byKey(const Key('confirm-deny')));
      await t.pump(const Duration(milliseconds: 100));
      await t.pumpAndSettle();
      expect(rig.api.where('POST', '/tasks/t-1/deny'), hasLength(1));
      expect(rig.api.where('POST', '/tasks/t-1/confirm'), isEmpty);
      expect(rig.api.where('GET', '/tasks/'), isEmpty);
      expect(find.textContaining('Denied'), findsOneWidget);
    });

    testWidgets('a task that fails after approval shows its error', (t) async {
      await open(t, remote: true, routes: (api) {
        api.routes['POST /devices/laptop-1/invoke'] = (_) => awaiting();
        api.routes['POST /tasks/t-1/confirm'] = (_) => {'ok': true};
        api.routes['GET /tasks/t-1'] = (_) => {'task': {'status': 'failed', 'error': 'camera busy'}};
      });
      await invoke(t);
      await t.tap(find.byKey(const Key('confirm-allow')));
      await t.pump(const Duration(milliseconds: 100));
      await t.pumpAndSettle();
      expect(find.textContaining('camera busy'), findsOneWidget);
    });

    testWidgets('an offline remote device is reported, nothing is sent', (t) async {
      final rig = await open(t, remote: true, connected: false);
      await captureLogs(() async {
        await invoke(t);
        await t.pumpAndSettle();
      });
      expect(find.textContaining('ThinkPad is offline'), findsOneWidget);
      expect(rig.api.where('POST', '/devices'), isEmpty);
    });
  });

  group('this device', () {
    testWidgets('preflight allow: runs natively', (t) async {
      final rig = await open(t, remote: false, routes: (api) {
        api.routes['POST /authorize/preflight'] = (_) => {'decision': 'allow'};
      });
      await invoke(t);
      await t.pumpAndSettle();
      expect(rig.dispatcher.invoked.single['capability'], 'camera.capture');
      expect(find.textContaining('/sdcard/photo.jpg', findRichText: true), findsWidgets);
      expect(rig.api.where('POST', '/authorize/preflight').single.body!['device_id'], me);
    });

    testWidgets('preflight deny: never runs, reason shown', (t) async {
      final rig = await open(t, remote: false, admin: true, routes: (api) {
        api.routes['POST /authorize/preflight'] = (_) => {'decision': 'deny', 'reason': 'camera is disabled by policy'};
      });
      await invoke(t);
      await t.pumpAndSettle();
      expect(rig.dispatcher.invoked, isEmpty);
      expect(find.textContaining('camera is disabled by policy'), findsOneWidget);
    });

    testWidgets('an unknown decision is treated as deny', (t) async {
      final rig = await open(t, remote: false, routes: (api) {
        api.routes['POST /authorize/preflight'] = (_) => {'decision': 'maybe'};
      });
      await invoke(t);
      await t.pumpAndSettle();
      expect(rig.dispatcher.invoked, isEmpty);
    });

    testWidgets('preflight confirm: dialog (no "always"), approval, then runs via the server', (t) async {
      final rig = await open(t, remote: false, routes: (api) {
        api.routes['POST /authorize/preflight'] = (_) => {'decision': 'confirm'};
        api.routes['POST /devices/$me/invoke'] = (_) => awaiting(scopes: const ['once', 'session', 'always']);
        api.routes['POST /tasks/t-1/confirm'] = (_) => {'ok': true};
        api.routes['GET /tasks/t-1'] = (_) => completed({'path': '/sdcard/x.jpg'});
      });
      await invoke(t);
      expect(find.byKey(const Key('confirm-title')), findsOneWidget);
      expect(find.byKey(const Key('scope-session')), findsOneWidget);
      expect(find.byKey(const Key('scope-always')), findsNothing);
      expect(rig.dispatcher.invoked, isEmpty); // not natively, even after the dialog opens
      await t.tap(find.byKey(const Key('confirm-allow')));
      await t.pump(const Duration(milliseconds: 100));
      await t.pumpAndSettle();
      expect(find.textContaining('/sdcard/x.jpg', findRichText: true), findsWidgets);
      expect(rig.dispatcher.invoked, isEmpty);
    });

    testWidgets('preflight confirm + deny: nothing runs', (t) async {
      final rig = await open(t, remote: false, routes: (api) {
        api.routes['POST /authorize/preflight'] = (_) => {'decision': 'confirm'};
        api.routes['POST /devices/$me/invoke'] = (_) => awaiting();
        api.routes['POST /tasks/t-1/deny'] = (_) => {'ok': true};
      });
      await invoke(t);
      await t.tap(find.byKey(const Key('confirm-deny')));
      await t.pump(const Duration(milliseconds: 100));
      await t.pumpAndSettle();
      expect(rig.dispatcher.invoked, isEmpty);
      expect(find.textContaining('Denied'), findsOneWidget);
    });

    testWidgets('server unreachable + non-admin: refused, nothing runs', (t) async {
      final rig = await open(t, remote: false, admin: false, routes: (api) {
        api.routes['POST /authorize/preflight'] = (_) => throw Unreachable();
      });
      await captureLogs(() async {
        await invoke(t);
        await t.pumpAndSettle();
      });
      expect(rig.dispatcher.invoked, isEmpty);
      expect(find.textContaining('only admins can run actions without a permission check'), findsOneWidget);
    });

    testWidgets('server unreachable + admin: may run natively', (t) async {
      final rig = await open(t, remote: false, admin: true, routes: (api) {
        api.routes['POST /authorize/preflight'] = (_) => throw ApiException(503, {'detail': 'down'});
      });
      await captureLogs(() async {
        await invoke(t);
        await t.pumpAndSettle();
      });
      expect(rig.dispatcher.invoked, hasLength(1));
    });

    testWidgets('server unreachable and /auth/me never answered: treated as non-admin', (t) async {
      final rig = await open(t, remote: false, routes: (api) {
        api.routes['GET /auth/me'] = (_) => throw Unreachable();
        api.routes['POST /authorize/preflight'] = (_) => throw Unreachable();
      });
      await captureLogs(() async {
        await invoke(t);
        await t.pumpAndSettle();
      });
      expect(rig.dispatcher.invoked, isEmpty);
    });
  });

  testWidgets('the Permissions shortcut opens the permissions screen', (t) async {
    await open(t, remote: true, routes: (api) {
      api.routes['GET /consents'] = (_) => {'consents': []};
    });
    await t.tap(find.byKey(const Key('open-permissions')));
    await t.pump();
    await t.pump(const Duration(milliseconds: 400));
    expect(find.text('NO SAVED PERMISSIONS'), findsOneWidget);
  });
}
