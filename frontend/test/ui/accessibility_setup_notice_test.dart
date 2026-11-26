import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/ui/plugins/widgets/accessibility_setup_notice.dart';

Widget host(Widget child) => MaterialApp(home: Scaffold(body: child));

void main() {
  testWidgets('off: explains the switch and opens Android settings', (tester) async {
    var opened = 0;
    await tester.pumpWidget(host(AccessibilitySetupNotice(
      statusProbe: () async => (enabled: false, connected: false),
      openSettings: () async { opened++; return true; },
    )));
    await tester.pumpAndSettle();
    expect(find.text('Turn on computer control'), findsOneWidget);
    expect(find.textContaining('Settings > Accessibility'), findsOneWidget);
    await tester.tap(find.byKey(const Key('open-accessibility-settings')));
    expect(opened, 1);
  });

  testWidgets('on: says so and offers no button', (tester) async {
    await tester.pumpWidget(host(AccessibilitySetupNotice(statusProbe: () async => (enabled: true, connected: true))));
    await tester.pumpAndSettle();
    expect(find.text('Computer control is on'), findsOneWidget);
    expect(find.byKey(const Key('open-accessibility-settings')), findsNothing);
  });

  testWidgets('not Android (no answer): shows nothing at all', (tester) async {
    await tester.pumpWidget(host(AccessibilitySetupNotice(statusProbe: () async => null)));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('accessibility-setup-notice')), findsNothing);
  });
}
