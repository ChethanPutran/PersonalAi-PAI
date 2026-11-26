import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:pai_mobile/ui/profile/server_section.dart';

Widget host({required bool signedIn, required List<String?> applied, bool custom = false, http.Client? client}) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: ServerSection(
            currentUrl: 'http://10.0.2.2:8000',
            defaultUrl: 'http://10.0.2.2:8000',
            usingCustom: custom,
            signedIn: signedIn,
            onApply: (u) async => applied.add(u),
            client: client ?? MockClient((_) async => http.Response('{"status":"healthy"}', 200)),
          ),
        ),
      ),
    );

void main() {
  testWidgets('invalid text shows an error and Save stays off', (t) async {
    final applied = <String?>[];
    await t.pumpWidget(host(signedIn: false, applied: applied));
    await t.enterText(find.byKey(const Key('server-field')), 'not a host');
    await t.pump();
    expect(find.textContaining('spaces'), findsOneWidget);
    expect(t.widget<FilledButton>(find.byKey(const Key('server-save'))).onPressed, isNull);
  });

  testWidgets('test connection reports success, save applies the cleaned address', (t) async {
    final applied = <String?>[];
    await t.pumpWidget(host(signedIn: false, applied: applied));
    await t.enterText(find.byKey(const Key('server-field')), ' 192.168.1.20/ ');
    await t.pump();
    await t.tap(find.byKey(const Key('server-test')));
    await t.pumpAndSettle();
    expect(find.textContaining('Connected'), findsOneWidget);
    await t.tap(find.byKey(const Key('server-save')));
    await t.pumpAndSettle();
    expect(applied, ['http://192.168.1.20:8000']);
  });

  testWidgets('a signed-in user is told they will be signed out and can cancel', (t) async {
    final applied = <String?>[];
    await t.pumpWidget(host(signedIn: true, applied: applied));
    await t.enterText(find.byKey(const Key('server-field')), 'https://pai.example.com');
    await t.pump();
    await t.tap(find.byKey(const Key('server-save')));
    await t.pumpAndSettle();
    expect(find.textContaining('You will be signed out'), findsOneWidget);
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    expect(applied, isEmpty);
    await t.tap(find.byKey(const Key('server-save')));
    await t.pumpAndSettle();
    await t.tap(find.byKey(const Key('server-save-confirm')));
    await t.pumpAndSettle();
    expect(applied, ['https://pai.example.com']);
  });

  testWidgets('plain http to a public host shows a warning; an unreachable server needs a second confirmation', (t) async {
    final applied = <String?>[];
    await t.pumpWidget(host(
        signedIn: false, applied: applied, client: MockClient((_) async => throw Exception('refused'))));
    await t.enterText(find.byKey(const Key('server-field')), 'pai.example.com');
    await t.pump();
    expect(find.byKey(const Key('server-http-warning')), findsOneWidget);
    await t.tap(find.byKey(const Key('server-save')));
    await t.pumpAndSettle();
    expect(find.textContaining('The test did not succeed'), findsOneWidget);
    await t.tap(find.byKey(const Key('server-save-confirm')));
    await t.pumpAndSettle();
    expect(applied, ['http://pai.example.com:8000']);
  });

  testWidgets('reset to default only when a custom server is set', (t) async {
    final applied = <String?>[];
    await t.pumpWidget(host(signedIn: false, applied: applied));
    expect(find.byKey(const Key('server-reset')), findsNothing);
    await t.pumpWidget(host(signedIn: false, applied: applied, custom: true));
    await t.tap(find.byKey(const Key('server-reset')));
    await t.pumpAndSettle();
    await t.tap(find.byKey(const Key('server-reset-confirm')));
    await t.pumpAndSettle();
    expect(applied, [null]);
  });
}
