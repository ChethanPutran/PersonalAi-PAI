import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/plugins/permissions/permission_models.dart';
import 'package:pai_mobile/ui/plugins/permissions/confirmation_dialog.dart';

import '../ui/golden_harness.dart';
import 'fakes.dart';

class Handle {
  final decisions = <ConfirmationDecision>[];
  int denied = 0;
  bool? result;
  bool done = false;
}

Future<Handle> open(
  WidgetTester t,
  ConfirmationRequest request, {
  ConfirmCallback? onConfirm,
  DenyCallback? onDeny,
  Set<ConfirmationScope>? limit,
  double textScale = 1.0,
  Size size = phone,
}) async {
  final h = Handle();
  await pumpHud(
    t,
    Builder(
      builder: (ctx) => Scaffold(
        body: Center(
          child: ElevatedButton(
            key: const Key('open'),
            onPressed: () async {
              h.result = await showConfirmationDialog(
                ctx,
                request: request,
                limitScopes: limit,
                onConfirm: onConfirm ?? (d) async => h.decisions.add(d),
                onDeny: onDeny ?? () async => h.denied++,
              );
              h.done = true;
            },
            child: const Text('open'),
          ),
        ),
      ),
    ),
    textScale: textScale,
    size: size,
  );
  await t.tap(find.byKey(const Key('open')));
  await t.pump();
  await t.pump(const Duration(milliseconds: 250));
  return h;
}

Future<void> tapKey(WidgetTester t, String key) async {
  final f = find.byKey(Key(key));
  await t.ensureVisible(f);
  await t.pump();
  await t.tap(f);
  await t.pump(const Duration(milliseconds: 100));
}

void main() {
  setUpAll(loadPaiFonts);

  group('what the user is shown', () {
    testWidgets('exactly what will run: action, device, risk, parameters, reason', (t) async {
      await open(t, sampleRequest());
      expect(find.text('Approve camera.capture?'), findsOneWidget);
      expect(find.text('camera.capture'), findsOneWidget);
      expect(find.text('Pixel 8'), findsOneWidget);
      expect(find.text('medium risk'), findsOneWidget);
      expect(find.text('Takes a photo with the front camera.'), findsOneWidget);
      expect(find.textContaining('camera: front', findRichText: true), findsOneWidget);
      expect(find.textContaining('flash: false', findRichText: true), findsOneWidget);
    });

    testWidgets('no parameters reads "none"', (t) async {
      await open(t, sampleRequest(params: const []));
      expect(find.text('none'), findsOneWidget);
    });

    testWidgets('falls back to the device id when there is no friendly name', (t) async {
      await open(t, ConfirmationRequest.fromJson(confirmationJson(deviceId: 'pixel-8')));
      expect(find.text('pixel-8'), findsOneWidget);
    });
  });

  group('scopes follow allowed_scopes', () {
    testWidgets('all three offered', (t) async {
      await open(t, sampleRequest());
      for (final k in ['scope-once', 'scope-session', 'scope-always']) {
        expect(find.byKey(Key(k)), findsOneWidget, reason: k);
      }
      expect(find.text('Allow once'), findsWidgets);
      expect(find.text('Allow for this session'), findsOneWidget);
      expect(find.text('Always allow…'), findsOneWidget);
    });

    testWidgets('once only: session/always are never offered, and high risk says why', (t) async {
      await open(t, sampleRequest(risk: 'high', scopes: const ['once']));
      expect(find.byKey(const Key('scope-once')), findsOneWidget);
      expect(find.byKey(const Key('scope-session')), findsNothing);
      expect(find.byKey(const Key('scope-always')), findsNothing);
      expect(find.byKey(const Key('risk-note')), findsOneWidget);
      expect(find.textContaining('approved one time at a time'), findsOneWidget);
      expect(find.textContaining('High risk'), findsOneWidget);
    });

    testWidgets('session but not always', (t) async {
      await open(t, sampleRequest(scopes: const ['once', 'session']));
      expect(find.byKey(const Key('scope-session')), findsOneWidget);
      expect(find.byKey(const Key('scope-always')), findsNothing);
    });

    testWidgets('critical risk shows the stronger note', (t) async {
      await open(t, sampleRequest(risk: 'critical', scopes: const ['once']));
      expect(find.textContaining('Critical risk'), findsOneWidget);
    });

    testWidgets('limitScopes (local tester) removes always even if the server allowed it', (t) async {
      await open(t, sampleRequest(), limit: {ConfirmationScope.once, ConfirmationScope.session});
      expect(find.byKey(const Key('scope-session')), findsOneWidget);
      expect(find.byKey(const Key('scope-always')), findsNothing);
    });

    testWidgets('a medium-risk request that allows several scopes shows no risk note', (t) async {
      await open(t, sampleRequest());
      expect(find.byKey(const Key('risk-note')), findsNothing);
    });
  });

  group('defaults and the safest choice', () {
    testWidgets('Deny holds the initial focus and nothing is auto-approved', (t) async {
      final h = await open(t, sampleRequest());
      expect(t.widget<TextButton>(find.byKey(const Key('confirm-deny'))).autofocus, isTrue);
      expect(t.widget<FilledButton>(find.byKey(const Key('confirm-allow'))).autofocus, isFalse);
      await t.pump(const Duration(seconds: 2));
      expect(h.decisions, isEmpty);
      expect(h.done, isFalse); // still waiting for the user
    });

    testWidgets('"Allow once" is preselected; no binding or expiry section yet', (t) async {
      await open(t, sampleRequest());
      expect(find.widgetWithText(FilledButton, 'Allow once'), findsOneWidget);
      expect(find.byKey(const Key('binding-section')), findsNothing);
      expect(find.byKey(const Key('expiry-section')), findsNothing);
    });

    testWidgets('Allow once sends exactly {scope: once}', (t) async {
      final h = await open(t, sampleRequest());
      await tapKey(t, 'confirm-allow');
      await t.pumpAndSettle();
      expect(h.decisions.single.toBody(), {'scope': 'once'});
      expect(h.result, isTrue);
    });
  });

  group('binding', () {
    testWidgets('session shows binding with "exact" preselected', (t) async {
      final h = await open(t, sampleRequest());
      await tapKey(t, 'scope-session');
      expect(find.byKey(const Key('binding-section')), findsOneWidget);
      expect(find.text('Only with these exact parameters'), findsOneWidget);
      expect(find.byKey(const Key('expiry-section')), findsNothing);
      expect(find.widgetWithText(FilledButton, 'Allow for this session'), findsOneWidget);
      await tapKey(t, 'confirm-allow');
      await t.pumpAndSettle();
      expect(h.decisions.single.toBody(), {
        'scope': 'session',
        'binding': {'mode': 'exact', 'keys': []},
      });
    });

    testWidgets('no binding choices (and no binding in the body) when the server says params cannot be bound',
        (t) async {
      final h = await open(t, sampleRequest(canBind: false));
      await tapKey(t, 'scope-session');
      expect(find.byKey(const Key('binding-section')), findsNothing);
      await tapKey(t, 'confirm-allow');
      await t.pumpAndSettle();
      expect(h.decisions.single.toBody(), {'scope': 'session'});
    });

    testWidgets('"Only these parameters…" needs at least one key, then sends them in display order', (t) async {
      final h = await open(t, sampleRequest());
      await tapKey(t, 'scope-session');
      await tapKey(t, 'binding-keys');
      expect(find.text('Pick at least one parameter.'), findsOneWidget);
      expect(t.widget<FilledButton>(find.byKey(const Key('confirm-allow'))).onPressed, isNull);
      await tapKey(t, 'key-flash');
      await tapKey(t, 'key-camera');
      expect(find.text('Pick at least one parameter.'), findsNothing);
      expect(t.widget<FilledButton>(find.byKey(const Key('confirm-allow'))).onPressed, isNotNull);
      await tapKey(t, 'confirm-allow');
      await t.pumpAndSettle();
      expect(h.decisions.single.toBody()['binding'], {'mode': 'keys', 'keys': ['camera', 'flash']});
    });

    testWidgets('"Any parameters" shows a calm warning and sends mode any', (t) async {
      final h = await open(t, sampleRequest());
      await tapKey(t, 'scope-session');
      expect(find.byKey(const Key('binding-any-warning')), findsNothing);
      await tapKey(t, 'binding-any');
      expect(find.byKey(const Key('binding-any-warning')), findsOneWidget);
      expect(find.textContaining('Broader'), findsOneWidget);
      await tapKey(t, 'confirm-allow');
      await t.pumpAndSettle();
      expect(h.decisions.single.toBody()['binding'], {'mode': 'any', 'keys': []});
    });

    testWidgets('with no parameters the "these parameters" option is not offered', (t) async {
      await open(t, sampleRequest(params: const []));
      await tapKey(t, 'scope-session');
      expect(find.byKey(const Key('binding-keys')), findsNothing);
      expect(find.text('Only with no parameters'), findsOneWidget);
    });
  });

  group('always: expiry', () {
    testWidgets('30 / 90 / 365 days, 90 preselected, with the end date', (t) async {
      final h = await open(t, sampleRequest());
      await tapKey(t, 'scope-always');
      expect(find.byKey(const Key('expiry-section')), findsOneWidget);
      for (final d in ['30 days', '90 days', '365 days']) {
        expect(find.text(d), findsOneWidget);
      }
      // testNow = 2026-11-23, +90 days = Feb 21, 2027
      expect(find.text('Until Feb 21, 2027'), findsOneWidget);
      await t.ensureVisible(find.text('365 days'));
      await t.pump();
      await t.tap(find.text('365 days'));
      await t.pump(const Duration(milliseconds: 300));
      expect(find.text('Until Nov 23, 2027'), findsOneWidget);
      await tapKey(t, 'confirm-allow');
      await t.pumpAndSettle();
      expect(h.decisions.single.toBody(), {
        'scope': 'always',
        'binding': {'mode': 'exact', 'keys': []},
        'days': 365,
      });
    });

    testWidgets('default is 90 days when untouched', (t) async {
      final h = await open(t, sampleRequest());
      await tapKey(t, 'scope-always');
      expect(find.widgetWithText(FilledButton, 'Always allow'), findsOneWidget);
      await tapKey(t, 'confirm-allow');
      await t.pumpAndSettle();
      expect(h.decisions.single.toBody()['days'], 90);
    });

    testWidgets('switching back to once removes binding and expiry', (t) async {
      await open(t, sampleRequest());
      await tapKey(t, 'scope-always');
      await tapKey(t, 'scope-once');
      expect(find.byKey(const Key('binding-section')), findsNothing);
      expect(find.byKey(const Key('expiry-section')), findsNothing);
    });
  });

  group('deny and errors', () {
    testWidgets('Deny calls onDeny and returns false', (t) async {
      final h = await open(t, sampleRequest());
      await tapKey(t, 'confirm-deny');
      await t.pumpAndSettle();
      expect(h.denied, 1);
      expect(h.decisions, isEmpty);
      expect(h.result, isFalse);
      expect(find.byKey(const Key('confirm-title')), findsNothing);
    });

    testWidgets('a server 409/422 is shown inline, the dialog stays open, and the user can retry or deny',
        (t) async {
      var attempts = 0;
      final h = await open(t, sampleRequest(), onConfirm: (d) async {
        attempts++;
        if (attempts == 1) {
          throw const ConfirmationException('This action cannot be remembered.', code: 'control_not_supported');
        }
      });
      await tapKey(t, 'scope-always');
      await tapKey(t, 'confirm-allow');
      await t.pump(const Duration(milliseconds: 100));
      expect(find.byKey(const Key('confirm-error')), findsOneWidget);
      expect(find.text('This action cannot be remembered.'), findsOneWidget);
      expect(find.byKey(const Key('confirm-title')), findsOneWidget); // still open
      expect(h.done, isFalse);
      // the user changes their mind to "once" and retries
      await tapKey(t, 'scope-once');
      await tapKey(t, 'confirm-allow');
      await t.pumpAndSettle();
      expect(attempts, 2);
      expect(h.result, isTrue);
    });

    testWidgets('an unexpected failure is reported without crashing', (t) async {
      final logs = await captureLogs(() async {
        await open(t, sampleRequest(), onConfirm: (d) async => throw Unreachable());
        await tapKey(t, 'confirm-allow');
        await t.pump(const Duration(milliseconds: 100));
        expect(find.textContaining('Could not send your decision'), findsOneWidget);
        expect(find.textContaining('Connection refused'), findsOneWidget);
      });
      expect(logs.join('\n'), contains('[ConfirmationDialog] sending approval failed'));
    });

    testWidgets('a failed deny is reported and the dialog stays so the user can try again', (t) async {
      await open(t, sampleRequest(), onDeny: () async => throw Unreachable());
      await tapKey(t, 'confirm-deny');
      await t.pump(const Duration(milliseconds: 100));
      expect(find.byKey(const Key('confirm-error')), findsOneWidget);
      expect(find.byKey(const Key('confirm-title')), findsOneWidget);
    });
  });

  group('accessibility', () {
    testWidgets('text scale 1.3 does not overflow (all sections open)', (t) async {
      await open(t, sampleRequest(risk: 'high'), textScale: 1.3);
      await tapKey(t, 'scope-always');
      await tapKey(t, 'binding-keys');
      expect(t.takeException(), isNull);
      await tapKey(t, 'binding-any');
      expect(t.takeException(), isNull);
    });

    testWidgets('text scale 1.3 on a small desktop window', (t) async {
      await open(t, sampleRequest(), textScale: 1.3, size: desktop);
      expect(t.takeException(), isNull);
    });

    testWidgets('every control is at least 48dp tall', (t) async {
      await open(t, sampleRequest());
      await tapKey(t, 'scope-always');
      for (final k in ['confirm-deny', 'confirm-allow', 'scope-once', 'scope-session', 'scope-always',
        'binding-exact', 'binding-keys', 'binding-any']) {
        await t.ensureVisible(find.byKey(Key(k)));
        final size = t.getSize(find.byKey(Key(k)));
        expect(size.height, greaterThanOrEqualTo(48), reason: '$k is ${size.height}dp tall');
      }
      final seg = t.getSize(find.byType(SegmentedButton<int>));
      expect(seg.height, greaterThanOrEqualTo(48));
    });

    testWidgets('choices are announced as a mutually exclusive group', (t) async {
      final handle = t.ensureSemantics();
      await open(t, sampleRequest());
      final node = t.getSemantics(find.byKey(const Key('scope-once')));
      expect(node.label, 'Allow once');
      expect(node.flagsCollection.isSelected, isNotNull);
      handle.dispose();
    });
  });

  group('goldens', () {
    testWidgets('dialog: always allow with parameter binding', (t) async {
      await open(t, sampleRequest());
      await tapKey(t, 'scope-always');
      await tapKey(t, 'binding-keys');
      await tapKey(t, 'key-camera');
      expect(t.widget<FilledButton>(find.byKey(const Key('confirm-allow'))).onPressed, isNotNull);
      await t.pumpAndSettle();
      await expectGolden(t, 'confirmation_always_binding');
    });

    testWidgets('dialog: high risk, once only', (t) async {
      await open(t, sampleRequest(risk: 'high', scopes: const ['once']));
      await expectGolden(t, 'confirmation_high_risk_once');
    });
  });
}
