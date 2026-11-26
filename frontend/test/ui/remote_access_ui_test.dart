import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/device/remote_access.dart';
import 'package:pai_mobile/plugins/plugin_manager.dart';
import 'package:pai_mobile/profile/settings_service.dart';
import 'package:pai_mobile/ui/plugins/plugins_screen.dart';
import 'package:pai_mobile/ui/profile/profile_screen.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import 'fakes.dart';
import 'golden_harness.dart';

class _Api extends ApiClient {
  _Api() : super(baseUrl: 'http://test', apiPrefix: '/api/v1');
  final puts = <Map<String, dynamic>>[];
  bool fail = false;

  @override
  Future<dynamic> put(String path, Map<String, dynamic> body) async {
    if (fail) throw ApiException(500, {'detail': 'boom'});
    puts.add(body);
    return {
      'allow_remote_install': body['allow_remote_install'] ?? false,
      'allow_remote_invoke': body['allow_remote_invoke'] ?? false,
    };
  }
}

class _RemoteFake extends FakePluginManager {
  RemoteOp op = const RemoteOp();
  @override
  RemoteOp remoteStateFor(String deviceId, String pluginId) => pluginId == 'screen' ? op : const RemoteOp();
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

  group('Remote access switches (Profile)', () {
    Future<(RemoteAccessController, _Api)> pump(WidgetTester t) async {
      final api = _Api();
      final c = RemoteAccessController(api: api, deviceId: 'pixel-8');
      await pumpHud(
        t,
        ProfileScreen(
          service: FakeProfileService(),
          settings: SettingsService(initial: const {}),
          currentDeviceId: 'pixel-8',
          remoteAccess: c,
          onLogout: () async {},
        ),
        size: const Size(400, 1400),
      );
      await _settle(t);
      return (c, api);
    }

    testWidgets('both are off by default, each with a one-line explanation', (t) async {
      await pump(t);
      expect(find.text('Allow remote plugin install'), findsOneWidget);
      expect(find.text('Allow remote plugin use'), findsOneWidget);
      expect(find.textContaining('install and remove plugins on this one'), findsOneWidget);
      expect(find.textContaining('run plugins on this one'), findsOneWidget);
      expect(t.widget<SwitchListTile>(find.byKey(const ValueKey('remote-install-switch'))).value, isFalse);
      expect(t.widget<SwitchListTile>(find.byKey(const ValueKey('remote-invoke-switch'))).value, isFalse);
    });

    testWidgets('turning one on saves it and shows the value the backend returned', (t) async {
      final (c, api) = await pump(t);
      await t.tap(find.byKey(const ValueKey('remote-install-switch')));
      await _settle(t);
      expect(api.puts.single, {'allow_remote_install': true});
      expect(c.value, const RemoteAccess(install: true));
      expect(t.widget<SwitchListTile>(find.byKey(const ValueKey('remote-install-switch'))).value, isTrue);
      expect(t.widget<SwitchListTile>(find.byKey(const ValueKey('remote-invoke-switch'))).value, isFalse);
    });

    testWidgets('a change made elsewhere (pushed frame) shows up without a reload', (t) async {
      final (c, _) = await pump(t);
      c.applyFrame({'install': false, 'invoke': true});
      await _settle(t);
      expect(t.widget<SwitchListTile>(find.byKey(const ValueKey('remote-invoke-switch'))).value, isTrue);
    });

    testWidgets('a failed save leaves the switch where it was and explains in plain words', (t) async {
      final (c, api) = await pump(t);
      api.fail = true;
      await t.tap(find.byKey(const ValueKey('remote-install-switch')));
      await _settle(t);
      expect(t.widget<SwitchListTile>(find.byKey(const ValueKey('remote-install-switch'))).value, isFalse);
      expect(find.byKey(const ValueKey('remote-access-error')), findsOneWidget);
      expect(find.textContaining('boom'), findsNothing);
      expect(c.saving, isFalse);
    });

    testWidgets('without a controller the section is not shown', (t) async {
      await pumpHud(
        t,
        ProfileScreen(
          service: FakeProfileService(),
          settings: SettingsService(initial: const {}),
          currentDeviceId: 'pixel-8',
          onLogout: () async {},
        ),
        size: const Size(400, 1400),
      );
      await _settle(t);
      expect(find.text('Allow remote plugin install'), findsNothing);
    });
  });

  group('Remote install on the Plugins screen', () {
    Future<_RemoteFake> pump(WidgetTester t) async {
      final manager = _RemoteFake();
      await pumpHud(
        t,
        PluginsScreen(
          manager: manager,
          deviceRepository: FakeDeviceRepository([device('desk-linux', 'Desk Linux', 'linux')]),
          thisDevice: device('pixel-8', 'Pixel 8', 'android', current: true),
        ),
        size: const Size(400, 1600),
      );
      await _settle(t);
      return manager;
    }

    testWidgets('shows the live stage the device reports', (t) async {
      final manager = await pump(t);
      for (final (stage, label) in [
        ('downloading', 'Downloading…'),
        ('verifying', 'Verifying…'),
        ('installing', 'Installing…'),
        ('awaiting_permission', 'Waiting for permission on the device'),
      ]) {
        manager.op = RemoteOp(phase: RemotePhase.installing, stage: stage);
        manager.notifyListeners();
        await _settle(t);
        expect(find.textContaining(label, skipOffstage: false), findsOneWidget, reason: stage);
      }
    });

    testWidgets('a failure shows the clear message and hint with Retry, never a raw exception', (t) async {
      final manager = await pump(t);
      manager.op = const RemoteOp(
        phase: RemotePhase.failed,
        error: "Remote plugin installation is turned off on 'Desk Linux'.",
        hint: 'Open Profile on that device and turn on "Allow remote plugin install".',
      );
      manager.notifyListeners();
      await _settle(t);
      expect(find.byKey(const ValueKey('remote-error'), skipOffstage: false), findsOneWidget);
      expect(find.textContaining('turned off', skipOffstage: false), findsOneWidget);
      expect(find.byKey(const ValueKey('remote-hint'), skipOffstage: false), findsOneWidget);
      expect(find.text('Retry', skipOffstage: false), findsOneWidget);
      expect(find.textContaining('Exception', skipOffstage: false), findsNothing);
      expect(find.textContaining('ApiException', skipOffstage: false), findsNothing);
    });
  });
}
