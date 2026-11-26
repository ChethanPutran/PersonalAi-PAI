import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/profile/account_service.dart';
import 'package:pai_mobile/profile/settings_service.dart';
import 'package:pai_mobile/ui/profile/profile_screen.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import 'fakes.dart';
import 'golden_harness.dart';

/// An in-memory backend for /account/* and PATCH /devices/{id}.
class _Api extends ApiClient {
  _Api() : super(baseUrl: 'http://test', apiPrefix: '/api/v1');

  Map<String, dynamic> profile = {'id': 'u1', 'email': 'chethan@example.com', 'display_name': '', 'timezone': '', 'locale': ''};
  Map<String, dynamic> pers = {
    'preferred_name': '', 'about': '', 'occupation': '', 'likes': <String>[], 'dislikes': <String>[],
    'interests': <String>[], 'hobbies': <String>[], 'tone': 'friendly', 'detail': 'balanced',
  };
  final calls = <String>[];
  final bodies = <String, List<Map<String, dynamic>>>{};
  Object? failOn; // path substring that throws

  Future<dynamic> _maybeFail(String path) async {
    if (failOn != null && path.contains(failOn as String)) {
      throw ApiException(422, {'detail': 'Unknown time zone (use a name like Asia/Kolkata)'});
    }
  }

  @override
  Future<dynamic> get(String path, {Map<String, String>? query}) async {
    calls.add('GET $path');
    await _maybeFail(path);
    return path == '/account/profile' ? profile : pers;
  }

  @override
  Future<dynamic> patch(String path, Map<String, dynamic> body) async {
    calls.add('PATCH $path');
    (bodies[path] ??= []).add(body);
    await _maybeFail(path);
    if (path == '/account/profile') {
      profile = {...profile, ...body};
      return profile;
    }
    return {'id': 'pixel-8', 'name': body['name']};
  }

  @override
  Future<dynamic> put(String path, Map<String, dynamic> body) async {
    calls.add('PUT $path');
    (bodies[path] ??= []).add(body);
    await _maybeFail(path);
    pers = Map<String, dynamic>.from(body);
    return pers;
  }
}

Future<void> _settle(WidgetTester t) async {
  for (var i = 0; i < 8; i++) {
    await t.pump(const Duration(milliseconds: 60));
  }
}

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  Future<_Api> pump(WidgetTester t, {Size size = const Size(400, 3600)}) async {
    final api = _Api();
    await pumpHud(
      t,
      ProfileScreen(
        service: FakeProfileService(),
        settings: SettingsService(initial: const {}),
        currentDeviceId: 'pixel-8',
        account: AccountService(api: api),
        onLogout: () async {},
      ),
      size: size,
    );
    await _settle(t);
    return api;
  }

  Finder field(String key) => find.descendant(of: find.byKey(Key(key)), matching: find.byType(TextField));

  testWidgets('the three sections are there; without an AccountService they are not', (t) async {
    await pump(t);
    expect(find.text('ABOUT YOU'), findsOneWidget);
    expect(find.byKey(const Key('device-name-field')), findsOneWidget);
    expect(find.text('PERSONALIZATION'), findsOneWidget);
    expect(find.textContaining('Only used to personalise'), findsOneWidget);

    await pumpHud(
      t,
      ProfileScreen(service: FakeProfileService(), settings: SettingsService(initial: const {}), onLogout: () async {}),
      size: const Size(400, 1800),
    );
    await _settle(t);
    expect(find.text('ABOUT YOU'), findsNothing);
    expect(find.text('PERSONALIZATION'), findsNothing);
  });

  group('device name', () {
    testWidgets('is prefilled; Save is off until it changes; saving PATCHes the new name', (t) async {
      final api = await pump(t);
      expect(t.widget<TextField>(field('device-name-field')).controller!.text, 'Pixel 8');
      expect(t.widget<GlowButton>(find.byKey(const Key('device-name-save'))).onPressed, isNull);

      await t.enterText(field('device-name-field'), 'Chethan\'s phone');
      await t.pump();
      expect(t.widget<GlowButton>(find.byKey(const Key('device-name-save'))).onPressed, isNotNull);
      await t.tap(find.byKey(const Key('device-name-save')));
      await _settle(t);
      expect(api.bodies['/devices/pixel-8']!.single, {'name': 'Chethan\'s phone'});
      expect(find.text('Saved.'), findsWidgets);
      expect(t.widget<GlowButton>(find.byKey(const Key('device-name-save'))).onPressed, isNull); // clean again
    });

    testWidgets('an empty name is refused before anything is sent', (t) async {
      final api = await pump(t);
      await t.enterText(field('device-name-field'), '   ');
      await t.pump();
      await t.tap(find.byKey(const Key('device-name-save')));
      await _settle(t);
      expect(find.text('Give this device a name.'), findsOneWidget);
      expect(api.bodies.containsKey('/devices/pixel-8'), isFalse);
    });
  });

  group('about you', () {
    testWidgets('loads the profile and saves only what changed', (t) async {
      final api = await pump(t);
      await t.enterText(field('display-name-field'), 'Chethan R');
      await t.pump();
      await t.tap(find.byKey(const Key('about-you-save')));
      await _settle(t);
      expect(api.bodies['/account/profile']!.single, {'display_name': 'Chethan R'});
      expect(api.profile['display_name'], 'Chethan R');
    });

    testWidgets('time zone: type to search, tap a suggestion, save', (t) async {
      final api = await pump(t);
      await t.enterText(field('timezone-field'), 'kolk');
      await t.pump();
      expect(find.text('Asia/Kolkata'), findsOneWidget);
      await t.tap(find.text('Asia/Kolkata'));
      await t.pump();
      expect(t.widget<TextField>(field('timezone-field')).controller!.text, 'Asia/Kolkata');
      await t.tap(find.byKey(const Key('about-you-save')));
      await _settle(t);
      expect(api.bodies['/account/profile']!.single, {'timezone': 'Asia/Kolkata'});
    });

    testWidgets('the server\'s complaint is shown in words and nothing is marked saved', (t) async {
      final api = await pump(t);
      api.failOn = '/account/profile';
      await t.enterText(field('timezone-field'), 'Mars/Base');
      await t.pump();
      await t.tap(find.byKey(const Key('about-you-save')));
      await _settle(t);
      expect(find.textContaining('Unknown time zone'), findsWidgets);
      expect(t.widget<GlowButton>(find.byKey(const Key('about-you-save'))).onPressed, isNotNull); // still dirty
    });
  });

  group('personalization', () {
    testWidgets('chips: add with enter, ignore duplicates, split commas, remove with the x', (t) async {
      await pump(t);
      Finder likes() => find.descendant(of: find.ancestor(of: find.text('Likes'), matching: find.byType(Column)).first, matching: find.byType(TextField));
      await t.enterText(likes(), 'jazz');
      await t.testTextInput.receiveAction(TextInputAction.done);
      await t.pump();
      expect(find.byKey(const ValueKey('chip-Likes-jazz')), findsOneWidget);

      await t.enterText(likes(), 'JAZZ');                       // duplicate (case-insensitive)
      await t.testTextInput.receiveAction(TextInputAction.done);
      await t.pump();
      expect(find.byType(InputChip), findsOneWidget);

      await t.enterText(likes(), 'tea, chess ,');               // pasted list
      await t.testTextInput.receiveAction(TextInputAction.done);
      await t.pump();
      expect(find.byKey(const ValueKey('chip-Likes-tea')), findsOneWidget);
      expect(find.byKey(const ValueKey('chip-Likes-chess')), findsOneWidget);

      await t.tap(find.descendant(of: find.byKey(const ValueKey('chip-Likes-tea')), matching: find.byType(Icon)).last);
      await t.pump();
      expect(find.byKey(const ValueKey('chip-Likes-tea')), findsNothing);
    });

    testWidgets('fill in, choose tone and detail, Save sends the whole shape; then it is clean', (t) async {
      final api = await pump(t);
      expect(t.widget<GlowButton>(find.byKey(const Key('personalization-save'))).onPressed, isNull);

      await t.enterText(field('preferred-name-field'), 'Chet');
      await t.enterText(field('occupation-field'), 'PhD student');
      await t.enterText(field('about-field'), 'I work on assistants.');
      await t.tap(find.byKey(const Key('tone-concise')));
      await t.tap(find.byKey(const Key('detail-brief')));
      await t.pump();
      expect(t.widget<GlowButton>(find.byKey(const Key('personalization-save'))).onPressed, isNotNull);

      await t.tap(find.byKey(const Key('personalization-save')));
      await _settle(t);
      final body = api.bodies['/account/personalization']!.single;
      expect(body['preferred_name'], 'Chet');
      expect(body['occupation'], 'PhD student');
      expect(body['about'], 'I work on assistants.');
      expect((body['tone'], body['detail']), ('concise', 'brief'));
      expect(find.textContaining('personalise its replies'), findsWidgets);
      expect(t.widget<GlowButton>(find.byKey(const Key('personalization-save'))).onPressed, isNull);
    });

    testWidgets('existing values load, and "Clear everything" empties them (saved only on Save)', (t) async {
      final api = _Api();
      api.pers = {...api.pers, 'preferred_name': 'Chet', 'likes': ['jazz'], 'tone': 'formal'};
      await pumpHud(
        t,
        ProfileScreen(service: FakeProfileService(), settings: SettingsService(initial: const {}), currentDeviceId: 'pixel-8',
            account: AccountService(api: api), onLogout: () async {}),
        size: const Size(400, 3600),
      );
      await _settle(t);
      expect(t.widget<TextField>(field('preferred-name-field')).controller!.text, 'Chet');
      expect(find.byKey(const ValueKey('chip-Likes-jazz')), findsOneWidget);
      expect(t.widget<ChoiceChip>(find.byKey(const Key('tone-formal'))).selected, isTrue);

      await t.tap(find.byKey(const Key('personalization-clear')));
      await t.pump();
      expect(find.byKey(const ValueKey('chip-Likes-jazz')), findsNothing);
      expect(api.bodies.containsKey('/account/personalization'), isFalse); // nothing sent yet
      await t.tap(find.byKey(const Key('personalization-save')));
      await _settle(t);
      expect(api.bodies['/account/personalization']!.single['likes'], isEmpty);
      expect(api.bodies['/account/personalization']!.single['preferred_name'], '');
    });
  });

  // (A pre-existing 2 px overflow in the device list tile shows at 320 px wide with 1.3x text, with or
  // without these forms; the forms themselves are checked at sizes where the rest of the screen is clean.)
  for (final (name, size, scale) in [
    ('small phone (320 px)', const Size(320, 4200), 1.0),
    ('phone with large text', const Size(360, 4600), 1.3),
  ]) {
    testWidgets('the forms do not overflow on a $name', (t) async {
      await pumpHud(
        t,
        ProfileScreen(service: FakeProfileService(), settings: SettingsService(initial: const {}), currentDeviceId: 'pixel-8',
            account: AccountService(api: _Api()), onLogout: () async {}),
        size: size,
        textScale: scale,
      );
      await _settle(t);
      expect(t.takeException(), isNull);
    });
  }
}
