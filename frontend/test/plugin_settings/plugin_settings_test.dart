import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/plugins/plugin_settings.dart';
import 'package:pai_mobile/ui/plugins/plugin_settings_screen.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';
import 'package:pai_mobile/ui/plugins/widgets/all_files_access_notice.dart';
import 'package:pai_mobile/ui/plugins/widgets/plugin_install_dialog.dart';
import 'package:pai_mobile/ui/plugins/widgets/plugin_settings_form.dart';

const _own = '/storage/emulated/0/Android/data/com.example.pai_mobile/files';

/// Shaped like GET /plugins/device/{device}/{plugin}/config for the Android files plugin.
Map<String, dynamic> filesPayload({Map<String, dynamic>? effective}) => {
      'schema': {
        'type': 'object',
        'properties': {
          'allowedPaths': {
            'type': 'array',
            'title': 'Folders PAI may use',
            'description': 'PAI can list, read and (unless read-only) change files only inside these folders.',
            'items': {'type': 'string'},
            'default': [_own],
            'maxItems': 3,
            'x-pai': {
              'widget': 'paths',
              'suggestions': [
                {'label': 'Pictures', 'path': '/storage/emulated/0/Pictures'},
                {'label': 'Download', 'path': '/storage/emulated/0/Download'},
              ],
            },
          },
          'deniedPaths': {
            'type': 'array',
            'title': 'Folders PAI must never touch',
            'items': {'type': 'string'},
            'default': ['/storage/emulated/0/Android/data'],
            'x-pai': {'widget': 'paths'},
          },
          'readOnly': {'type': 'boolean', 'title': 'Read-only', 'description': 'No writing.', 'default': false},
        },
      },
      'values': <String, dynamic>{},
      'defaults': {
        'allowedPaths': [_own],
        'deniedPaths': ['/storage/emulated/0/Android/data'],
        'readOnly': false,
      },
      'effective': effective ??
          {
            'allowedPaths': [_own],
            'deniedPaths': ['/storage/emulated/0/Android/data'],
            'readOnly': false,
          },
    };

/// The HUD background animates forever, so pumpAndSettle would time out: pump a little instead.
Future<void> settle(WidgetTester t) async {
  await t.pump();
  await t.pump(const Duration(milliseconds: 200));
  await t.pump(const Duration(milliseconds: 200));
}

Future<void> tapKey(WidgetTester t, String key) async {
  final f = find.byKey(Key(key));
  await t.ensureVisible(f);
  await t.pump();
  await t.tap(f);
}

Widget wrap(Widget child) => MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child)));

void main() {
  group('PluginSettings.fromJson', () {
    test('reads fields, suggestions, defaults and effective values', () {
      final s = PluginSettings.fromJson(filesPayload());
      expect(s.hasSettings, isTrue);
      expect(s.fields.map((f) => f.key), ['allowedPaths', 'deniedPaths', 'readOnly']);
      final allowed = s.fields.first;
      expect(allowed.isPaths, isTrue);
      expect(allowed.maxItems, 3);
      expect(allowed.suggestions.map((e) => e.label), ['Pictures', 'Download']);
      expect(s.fields.last.kind, SettingKind.boolean);
      expect(s.effective['readOnly'], false);
    });
    test('a plugin without a schema has no settings', () {
      expect(PluginSettings.fromJson({'schema': {}, 'effective': {}}).hasSettings, isFalse);
      expect(PluginSettings.fromJson(null).hasSettings, isFalse);
    });
    test('humanizes a key without a title', () {
      expect(SettingField.humanize('maxFileSize'), 'Max File Size');
      expect(SettingField.humanize('read_only'), 'Read only');
    });
  });

  group('server errors', () {
    test('422 gives per-field errors', () {
      final e = PluginSettingsException.fromApi(ApiException(422, {
        'detail': {'code': 'invalid_plugin_config', 'errors': {'allowedPaths': 'must be an absolute path'}}
      }));
      expect(e.fieldErrors['allowedPaths'], contains('absolute'));
      expect(e.remoteOff, isFalse);
    });
    test('403 says remote install is off', () {
      final e = PluginSettingsException.fromApi(ApiException(403, {'detail': {'code': 'remote_install_disabled', 'message': 'Remote install is off.'}}));
      expect(e.remoteOff, isTrue);
      expect(e.message, 'Remote install is off.');
      expect(PluginSettingsException.fromApi(ApiException(403, {})).message, contains('remote plugin install'));
    });
    test('anything else is a plain message, never a raw exception', () {
      expect(PluginSettingsException.fromApi(Exception('socket')).message, contains("Couldn't reach"));
    });
  });

  group('settingsChanges and folderProblem', () {
    final s = PluginSettings.fromJson(filesPayload());
    test('only changed keys; back at default = null (reset)', () {
      final base = Map<String, dynamic>.from(s.effective);
      expect(settingsChanges(s, base, Map.of(base)), isEmpty);
      final cur = Map<String, dynamic>.from(base)..['readOnly'] = true;
      expect(settingsChanges(s, base, cur), {'readOnly': true});
      final saved = Map<String, dynamic>.from(base)..['readOnly'] = true;
      final back = Map<String, dynamic>.from(base);
      expect(settingsChanges(s, saved, back), {'readOnly': null});
    });
    test('folder rules', () {
      expect(folderProblem(''), isNotNull);
      expect(folderProblem('Download'), contains('starts with /'));
      expect(folderProblem('/a/../b'), isNotNull);
      expect(folderProblem('/storage/emulated/0/Download'), isNull);
      expect(folderProblem('~/Docs'), isNull);
    });
  });

  group('PluginSettingsForm', () {
    Future<Map<String, dynamic>> pump(WidgetTester t, {Map<String, String> errors = const {}, bool enabled = true}) async {
      final s = PluginSettings.fromJson(filesPayload());
      final latest = <String, dynamic>{};
      await t.pumpWidget(wrap(PluginSettingsForm(
        settings: s,
        initial: s.effective,
        errors: errors,
        enabled: enabled,
        onChanged: (v) {
          latest
            ..clear()
            ..addAll(v);
        },
      )));
      return latest;
    }

    testWidgets('shows titles, defaults, a switch and the folder list', (t) async {
      await pump(t);
      expect(find.text('Folders PAI may use'), findsOneWidget);
      expect(find.text(_own), findsOneWidget);
      expect(find.byKey(const Key('setting-switch-readOnly')), findsOneWidget);
      expect(find.byKey(const Key('setting-default-allowedPaths')), findsOneWidget);
      expect(find.textContaining('Default: off'), findsOneWidget);
    });

    testWidgets('a boolean toggles through the switch', (t) async {
      final latest = await pump(t);
      await tapKey(t, 'setting-switch-readOnly');
      await t.pump();
      expect(latest['readOnly'], true);
      expect(find.byKey(const Key('setting-reset-readOnly')), findsOneWidget);
      await tapKey(t, 'setting-reset-readOnly');
      await t.pump();
      expect(latest['readOnly'], false);
    });

    testWidgets('a suggestion chip adds a folder in one tap and is then disabled', (t) async {
      final latest = await pump(t);
      final chip = find.byKey(const Key('paths-suggest-allowedPaths-/storage/emulated/0/Pictures'));
      await t.ensureVisible(chip);
      await t.tap(chip);
      await t.pump();
      expect(latest['allowedPaths'], [_own, '/storage/emulated/0/Pictures']);
      expect(find.text('/storage/emulated/0/Pictures'), findsOneWidget);
      expect(t.widget<ActionChip>(chip).onPressed, isNull);
    });

    testWidgets('remove button removes a folder; an empty list says so', (t) async {
      final latest = await pump(t);
      await t.tap(find.byKey(const Key('paths-remove-allowedPaths-0')));
      await t.pump();
      expect(latest['allowedPaths'], isEmpty);
      expect(find.byKey(const Key('paths-empty-allowedPaths')), findsOneWidget);
    });

    testWidgets('typed folder is validated, trimmed, de-duplicated and added', (t) async {
      final latest = await pump(t);
      final field = find.byKey(const Key('paths-add-allowedPaths'));
      await t.ensureVisible(field);
      await t.enterText(field, 'Download');
      await t.tap(find.byKey(const Key('paths-add-btn-allowedPaths')));
      await t.pump();
      expect(find.textContaining('starts with /'), findsOneWidget);
      expect(latest['allowedPaths'], isNull);                       // nothing emitted for a rejected entry

      await t.enterText(field, '  /storage/emulated/0/Music/  ');
      await t.tap(find.byKey(const Key('paths-add-btn-allowedPaths')));
      await t.pump();
      expect(latest['allowedPaths'], [_own, '/storage/emulated/0/Music']);

      await t.enterText(field, _own);
      await t.tap(find.byKey(const Key('paths-add-btn-allowedPaths')));
      await t.pump();
      expect(find.textContaining('already in the list'), findsOneWidget);
    });

    testWidgets('maxItems stops adding', (t) async {
      final latest = await pump(t);
      for (final p in ['/a', '/b']) {
        final field = find.byKey(const Key('paths-add-allowedPaths'));
        await t.ensureVisible(field);
        await t.enterText(field, p);
        await t.tap(find.byKey(const Key('paths-add-btn-allowedPaths')));
        await t.pump();
      }
      expect((latest['allowedPaths'] as List).length, 3);
      expect(t.widget<FilledButton>(find.byKey(const Key('paths-add-btn-allowedPaths'))).onPressed, isNull);
    });

    testWidgets('server errors show under their field', (t) async {
      await pump(t, errors: {'deniedPaths': 'must not contain ".."'});
      expect(find.byKey(const Key('setting-error-deniedPaths')), findsOneWidget);
      expect(find.textContaining('must not contain'), findsOneWidget);
      expect(find.byKey(const Key('setting-error-allowedPaths')), findsNothing);
    });

    testWidgets('disabled form cannot be edited', (t) async {
      await pump(t, enabled: false);
      expect(t.widget<SwitchListTile>(find.byKey(const Key('setting-switch-readOnly'))).onChanged, isNull);
      expect(t.widget<IconButton>(find.byKey(const Key('paths-remove-allowedPaths-0'))).onPressed, isNull);
    });
  });

  group('PluginSettingsScreen', () {
    Widget screen({
      required Future<PluginSettings> Function() load,
      required Future<PluginSettings> Function(Map<String, dynamic>) save,
      bool isThis = true,
    }) =>
        MaterialApp(
          home: PluginSettingsScreen(pluginName: 'Files', deviceName: 'Desktop', isThisDevice: isThis, load: load, save: save),
        );

    testWidgets('loads, saves only the changes and confirms', (t) async {
      Map<String, dynamic>? sent;
      await t.pumpWidget(screen(
        load: () async => PluginSettings.fromJson(filesPayload()),
        save: (c) async {
          sent = c;
          return PluginSettings.fromJson(filesPayload(effective: {
            'allowedPaths': [_own],
            'deniedPaths': ['/storage/emulated/0/Android/data'],
            'readOnly': true
          }));
        },
      ));
      await settle(t);
      await tapKey(t, 'setting-switch-readOnly');
      await t.pump();
      await tapKey(t, 'settings-save');
      await settle(t);
      expect(sent, {'readOnly': true});
      expect(find.byKey(const Key('settings-message')), findsOneWidget);
      expect(find.text('Saved.'), findsOneWidget);
    });

    testWidgets('422 puts the error under the field and keeps the edit', (t) async {
      await t.pumpWidget(screen(
        load: () async => PluginSettings.fromJson(filesPayload()),
        save: (c) async => throw const PluginSettingsException('Some settings need fixing.', fieldErrors: {'allowedPaths': 'is not allowed'}),
      ));
      await settle(t);
      await tapKey(t, 'setting-switch-readOnly');
      await t.pump();
      await tapKey(t, 'settings-save');
      await settle(t);
      expect(find.byKey(const Key('setting-error-allowedPaths')), findsOneWidget);
      expect(find.text('Some settings need fixing.'), findsOneWidget);
    });

    testWidgets('403 on another device explains how to turn remote install on', (t) async {
      await t.pumpWidget(screen(
        isThis: false,
        load: () async => PluginSettings.fromJson(filesPayload()),
        save: (c) async => throw const PluginSettingsException('Remote install is off.', remoteOff: true),
      ));
      await settle(t);
      expect(find.text('On Desktop'), findsOneWidget);
      await tapKey(t, 'setting-switch-readOnly');
      await t.pump();
      await tapKey(t, 'settings-save');
      await settle(t);
      expect(find.text('Remote install is off.'), findsOneWidget);
      expect(find.byKey(const Key('settings-remote-hint')), findsOneWidget);
      expect(find.textContaining('Allow remote plugin install'), findsOneWidget);
    });

    testWidgets('a plugin without settings says so', (t) async {
      await t.pumpWidget(screen(load: () async => const PluginSettings(), save: (c) async => const PluginSettings()));
      await settle(t);
      expect(find.text('NO SETTINGS'), findsOneWidget);
    });

    testWidgets('a load failure is shown with a retry', (t) async {
      var calls = 0;
      await t.pumpWidget(screen(
        load: () async {
          calls++;
          if (calls == 1) throw const PluginSettingsException("Couldn't reach the server.");
          return PluginSettings.fromJson(filesPayload());
        },
        save: (c) async => const PluginSettings(),
      ));
      await settle(t);
      expect(find.textContaining("Couldn't reach"), findsWidgets);
    });

    testWidgets('Save stays off until something changed', (t) async {
      t.view.devicePixelRatio = 1;
      t.view.physicalSize = const Size(800, 2400);
      addTearDown(t.view.reset);
      await t.pumpWidget(screen(load: () async => PluginSettings.fromJson(filesPayload()), save: (c) async => const PluginSettings()));
      await settle(t);
      final save = find.byKey(const Key('settings-save'));
      expect(t.widget<GlowButton>(save).onPressed, isNull);
      await t.tap(find.byKey(const Key('setting-switch-readOnly')));
      await t.pump();
      expect(t.widget<GlowButton>(save).onPressed, isNotNull);
    });
  });

  group('install dialog', () {
    Future<void> open(WidgetTester t, Future<void> Function(Map<String, dynamic>) onInstall) async {
      final s = PluginSettings.fromJson(filesPayload());
      await t.pumpWidget(MaterialApp(
        home: Builder(
          builder: (ctx) => Scaffold(
            body: Center(
              child: ElevatedButton(
                onPressed: () => showPluginInstallDialog(ctx,
                    pluginName: 'Files', targetName: 'Phone', isThisDevice: false, settings: s, onInstall: onInstall),
                child: const Text('go'),
              ),
            ),
          ),
        ),
      ));
      await t.tap(find.text('go'));
      await t.pumpAndSettle();
    }

    testWidgets('install with defaults sends an empty map', (t) async {
      Map<String, dynamic>? sent;
      await open(t, (c) async => sent = c);
      expect(find.text('Install Files'), findsOneWidget);
      expect(find.textContaining('on Phone'), findsOneWidget);
      await tapKey(t, 'install-dialog-install');
      await t.pumpAndSettle();
      expect(sent, isEmpty);
      expect(find.byKey(const Key('plugin-install-dialog')), findsNothing);
    });

    testWidgets('chosen settings are sent; a refused value keeps the dialog open with the message', (t) async {
      var attempts = 0;
      Map<String, dynamic>? sent;
      await open(t, (c) async {
        attempts++;
        if (attempts == 1) throw const PluginSettingsException('Some settings need fixing.', fieldErrors: {'readOnly': 'nope'});
        sent = c;
      });
      await tapKey(t, 'setting-switch-readOnly');
      await t.pump();
      await tapKey(t, 'install-dialog-install');
      await t.pumpAndSettle();
      expect(find.byKey(const Key('install-dialog-message')), findsOneWidget);
      expect(find.byKey(const Key('setting-error-readOnly')), findsOneWidget);
      await tapKey(t, 'install-dialog-install');
      await t.pumpAndSettle();
      expect(sent, {'readOnly': true});
    });

    testWidgets('cancel installs nothing', (t) async {
      var called = false;
      await open(t, (c) async => called = true);
      await tapKey(t, 'install-dialog-cancel');
      await t.pumpAndSettle();
      expect(called, isFalse);
    });
  });

  group('AllFilesAccessNotice', () {
    testWidgets('off: explains where to switch it on and opens Android settings', (t) async {
      var opened = false;
      await t.pumpWidget(MaterialApp(
        home: Scaffold(body: AllFilesAccessNotice(statusProbe: () async => false, openSettings: () async => opened = true)),
      ));
      await t.pumpAndSettle();
      expect(find.textContaining('Special app access > All files access > PAI'), findsOneWidget);
      await t.tap(find.byKey(const Key('open-all-files-access')));
      expect(opened, isTrue);
    });
    testWidgets('on: says so, no button; unknown platform: hidden', (t) async {
      await t.pumpWidget(MaterialApp(home: Scaffold(body: AllFilesAccessNotice(key: const ValueKey('a'), statusProbe: () async => true))));
      await t.pumpAndSettle();
      expect(find.text('All files access is on'), findsOneWidget);
      expect(find.byKey(const Key('open-all-files-access')), findsNothing);
      await t.pumpWidget(MaterialApp(home: Scaffold(body: AllFilesAccessNotice(key: const ValueKey('b'), statusProbe: () async => null))));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('all-files-access-notice')), findsNothing);
    });
  });
}
