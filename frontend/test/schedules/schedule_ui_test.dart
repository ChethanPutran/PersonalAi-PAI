import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/models/schedule.dart';
import 'package:pai_mobile/models/schedule_capability.dart';
import 'package:pai_mobile/schedules/schedule_errors.dart';
import 'package:pai_mobile/ui/schedules/capability_picker.dart';
import 'package:pai_mobile/ui/schedules/consent_widgets.dart';
import 'package:pai_mobile/ui/schedules/schedule_card.dart';
import 'package:pai_mobile/ui/schedules/schedule_editor_sheet.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import '../ui/fakes.dart';
import '../ui/golden_harness.dart';
import 'fakes.dart';

Future<void> settle(WidgetTester t) async {
  for (var i = 0; i < 6; i++) {
    await t.pump(const Duration(milliseconds: 60));
  }
}

/// StatusChip, GlowButton and the HUD empty/error titles upper-case their text.
Finder chip(String label) => find.text(label.toUpperCase());
Finder btn(String label) => find.text(label.toUpperCase());

class Result<T> {
  T? value;
  bool done = false;
}

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  // ---------------------------------------------------------------- consent

  group('consent chips', () {
    testWidgets('each state has its own wording', (t) async {
      final exp = DateTime(2026, 12, 20, 10);
      final cases = {
        ConsentStatus.needsApproval: 'Needs approval',
        ConsentStatus.approved: 'Approved until Dec 20, 2026',
        ConsentStatus.expiringSoon: 'Expires soon · Dec 20, 2026',
        ConsentStatus.expired: 'Approval expired',
      };
      for (final e in cases.entries) {
        await pumpHud(t, Scaffold(body: Center(child: ConsentChip(status: e.key, expiresAt: exp))));
        expect(chip(e.value), findsOneWidget, reason: e.key.name);
      }
      await pumpHud(t, const Scaffold(body: Center(child: ConsentChip(status: ConsentStatus.notRequired))));
      expect(find.byType(StatusChip), findsNothing);
    });
  });

  group('approve dialog', () {
    ApprovalRequest request() => const ApprovalRequest(
          capability: 'camera.capture',
          deviceLabel: 'Pixel 8',
          parameters: {'name': 'sunrise', 'flash': true},
          scheduleLabel: 'Cron: 0 9 * * *',
          risk: ScheduleRisk.medium,
        );

    Future<Result<int>> open(WidgetTester t) async {
      final res = Result<int>();
      await pumpHud(
        t,
        Scaffold(
          body: Builder(
            builder: (ctx) => Center(
              child: ElevatedButton(
                onPressed: () async {
                  res.value = await showApproveConsentDialog(ctx, request());
                  res.done = true;
                },
                child: const Text('open'),
              ),
            ),
          ),
        ),
      );
      await t.tap(find.text('open'));
      await settle(t);
      return res;
    }

    testWidgets('states EXACTLY what will run, and defaults to 30 days', (t) async {
      await open(t);
      expect(find.text('Approve this to run on its own?'), findsOneWidget);
      expect(find.text('camera.capture'), findsOneWidget);
      expect(find.text('Pixel 8'), findsOneWidget);
      expect(find.textContaining('name: sunrise'), findsOneWidget);
      expect(find.textContaining('flash: true'), findsOneWidget);
      expect(find.text('Cron: 0 9 * * *'), findsOneWidget);
      expect(find.text('medium risk'), findsOneWidget);
      for (final d in ['7 days', '30 days', '90 days']) {
        expect(find.text(d), findsOneWidget);
      }
      // 30 is preselected: kNow + 30 days
      expect(find.text('Until Dec 23, 2026'), findsOneWidget);
    });

    testWidgets('Approve returns the chosen duration', (t) async {
      final res = await open(t);
      await t.tap(find.text('90 days'));
      await settle(t);
      expect(find.text('Until Feb 21, 2027'), findsOneWidget);
      await t.tap(find.widgetWithText(FilledButton, 'Approve'));
      await settle(t);
      expect(res.done, isTrue);
      expect(res.value, 90);
    });

    testWidgets('default Approve is 30 days; Cancel approves nothing', (t) async {
      var res = await open(t);
      await t.tap(find.widgetWithText(FilledButton, 'Approve'));
      await settle(t);
      expect(res.value, 30);

      res = await open(t);
      await t.tap(find.text('Cancel'));
      await settle(t);
      expect(res.done, isTrue);
      expect(res.value, isNull);
    });
  });

  // ------------------------------------------------------------ list card

  group('schedule card', () {
    Future<List<ScheduleMenuAction>> pumpCard(WidgetTester t, Schedule s,
        {VoidCallback? onApprove, double scale = 1.0, String deviceLabel = 'Pixel 8'}) async {
      final actions = <ScheduleMenuAction>[];
      await pumpHud(
        t,
        Scaffold(
          body: Padding(
            padding: const EdgeInsets.all(16),
            child: ScheduleCard(
              schedule: s,
              deviceLabel: deviceLabel,
              onToggle: () {},
              onApprove: onApprove,
              onMenu: actions.add,
            ),
          ),
        ),
        textScale: scale,
      );
      await settle(t);
      return actions;
    }

    testWidgets('a plain reminder shows no approval UI', (t) async {
      await pumpCard(
          t,
          makeSchedule(
              capability: kReminderCapability, requiresConsent: false, risk: ScheduleRisk.low,
              title: 'Call Mom', device: null, parameters: const {}),
          deviceLabel: 'any online device');
      expect(find.text('Call Mom'), findsOneWidget);
      expect(find.text('Reminder'), findsOneWidget);
      expect(find.text('on any online device'), findsOneWidget);
      expect(find.byKey(const Key('approval-row')), findsNothing);
      expect(find.textContaining('approval'), findsNothing);
    });

    testWidgets('a plugin action shows capability, risk, device, next run', (t) async {
      await pumpCard(t, makeSchedule(consent: approvedUntil(DateTime(2026, 12, 20))));
      expect(find.text('camera.capture'), findsOneWidget);
      expect(find.text('medium risk'), findsOneWidget);
      expect(find.text('on Pixel 8'), findsOneWidget);
      expect(find.textContaining('Next: Nov 24 09:00'), findsOneWidget);
      expect(chip('Approved until Dec 20, 2026'), findsOneWidget);
      expect(find.byKey(const Key('approval-row')), findsNothing);
      expect(find.byIcon(Icons.bolt), findsOneWidget);
    });

    testWidgets('needs approval: calm notice + Approve shortcut that calls back', (t) async {
      var approved = 0;
      await pumpCard(t, makeSchedule(), onApprove: () => approved++);
      expect(chip('Needs approval'), findsOneWidget);
      expect(find.text(kConsentNeededMessage), findsOneWidget);
      await t.tap(btn('Approve…'));
      expect(approved, 1);
    });

    testWidgets('expired shows the notice again; expiring soon does not nag', (t) async {
      await pumpCard(t, makeSchedule(consent: approvedUntil(DateTime(2026, 11, 20))), onApprove: () {});
      expect(chip('Approval expired'), findsOneWidget);
      expect(find.byKey(const Key('approval-row')), findsOneWidget);

      await pumpCard(t, makeSchedule(consent: approvedUntil(DateTime(2026, 11, 25))), onApprove: () {});
      expect(chip('Expires soon · Nov 25, 2026'), findsOneWidget);
      expect(find.byKey(const Key('approval-row')), findsNothing);
    });

    testWidgets('a firing skipped for lack of approval is flagged, with the Approve shortcut', (t) async {
      await pumpCard(
        t,
        makeSchedule(lastRunStatus: LastRunStatus.skippedNoConsent, lastRunAt: DateTime(2026, 11, 23, 9)),
        onApprove: () {},
      );
      expect(chip('Skipped · needs approval'), findsOneWidget);
      expect(find.textContaining('Last run Today 09:00'), findsOneWidget);
      expect(find.byKey(const Key('approval-row')), findsOneWidget);
    });

    testWidgets('last-run error is expandable', (t) async {
      await pumpCard(
        t,
        makeSchedule(
          consent: approvedUntil(DateTime(2026, 12, 20)),
          lastRunStatus: LastRunStatus.failed,
          lastRunAt: DateTime(2026, 11, 23, 9),
          lastRunError: 'Device pixel-8 did not respond in 60s',
        ),
      );
      expect(chip('Failed'), findsOneWidget);
      expect(find.byKey(const Key('last-run-error')), findsNothing);
      await t.tap(find.text('Details'));
      await settle(t);
      expect(find.text('Device pixel-8 did not respond in 60s'), findsOneWidget);
      await t.tap(find.text('Hide details'));
      await settle(t);
      expect(find.byKey(const Key('last-run-error')), findsNothing);
    });

    testWidgets('each last-run status has its own label', (t) async {
      final labels = {
        LastRunStatus.ok: 'Ran OK',
        LastRunStatus.denied: 'Denied',
        LastRunStatus.deviceOffline: 'Device offline',
        LastRunStatus.unknown: 'Unknown',
      };
      for (final e in labels.entries) {
        await pumpCard(t, makeSchedule(consent: approvedUntil(DateTime(2026, 12, 20)), lastRunStatus: e.key));
        expect(chip(e.value), findsOneWidget, reason: e.key.name);
      }
    });

    testWidgets('menu: Edit and Revoke approval (only when approved)', (t) async {
      var actions = await pumpCard(t, makeSchedule(consent: approvedUntil(DateTime(2026, 12, 20))));
      await t.tap(find.byType(PopupMenuButton<ScheduleMenuAction>));
      await settle(t);
      expect(find.text('Edit'), findsOneWidget);
      expect(find.text('Revoke approval'), findsOneWidget);
      await t.tap(find.text('Revoke approval'));
      await settle(t);
      expect(actions, [ScheduleMenuAction.revoke]);

      actions = await pumpCard(t, makeSchedule());
      await t.tap(find.byType(PopupMenuButton<ScheduleMenuAction>));
      await settle(t);
      expect(find.text('Revoke approval'), findsNothing);
      await t.tap(find.text('Edit'));
      await settle(t);
      expect(actions, [ScheduleMenuAction.edit]);
    });

    testWidgets('text scale 1.3 does not overflow, and tap targets are big enough', (t) async {
      await pumpCard(
        t,
        makeSchedule(
          lastRunStatus: LastRunStatus.failed,
          lastRunError: 'x' * 80,
          lastRunAt: DateTime(2026, 11, 23, 9),
        ),
        onApprove: () {},
        scale: 1.3,
      );
      expect(t.takeException(), isNull);
      expect(t.getSize(find.byType(PopupMenuButton<ScheduleMenuAction>)).shortestSide, greaterThanOrEqualTo(40));
      expect(t.getSize(find.text('Details').first.evaluate().isEmpty ? find.byType(GlowButton) : find.ancestor(
          of: find.text('Details'), matching: find.byType(InkWell)).first).height, greaterThanOrEqualTo(48));
      expect(t.getSize(find.byType(GlowButton)).height, greaterThanOrEqualTo(40));
    });
  });

  // ---------------------------------------------------------- capability picker

  group('capability picker', () {
    Future<Result<SchedulableCapability>> pumpPicker(WidgetTester t) async {
      final res = Result<SchedulableCapability>();
      await pumpHud(
        t,
        Scaffold(
          body: Padding(
            padding: const EdgeInsets.all(16),
            child: CapabilityPickerField(
              capabilities: sampleCaps().where((c) => !c.isReminder).toList(),
              selectedId: null,
              onSelected: (c) {
                res.value = c;
                res.done = true;
              },
            ),
          ),
        ),
      );
      await t.tap(find.text('Choose an action…'));
      await settle(t);
      return res;
    }

    testWidgets('lists actions grouped by plugin with risk chips', (t) async {
      await pumpPicker(t);
      for (final g in ['CAMERA', 'FILES', 'DESKTOP', 'SHELL']) {
        expect(find.text(g), findsOneWidget, reason: g);
      }
      expect(find.text('camera.capture'), findsOneWidget);
      expect(find.text('high risk'), findsOneWidget);
      expect(find.text('critical risk'), findsOneWidget);
    });

    testWidgets('search filters by id, description and plugin', (t) async {
      await pumpPicker(t);
      await t.enterText(find.byType(TextField).last, 'photo');
      await settle(t);
      expect(find.text('camera.capture'), findsOneWidget);
      expect(find.text('files.read'), findsNothing);
      await t.enterText(find.byType(TextField).last, 'zzz');
      await settle(t);
      expect(btn('No matching actions'), findsOneWidget);
    });

    testWidgets('actions that cannot be scheduled are disabled and say why', (t) async {
      final res = await pumpPicker(t);
      expect(find.text('Shell commands cannot run unattended.'), findsOneWidget);
      expect(find.byIcon(Icons.lock_outline), findsOneWidget);
      await t.tap(find.text('shell.exec'));
      await settle(t);
      expect(res.done, isFalse); // nothing selected
      expect(find.text('shell.exec'), findsOneWidget); // sheet still open
      await t.tap(find.text('camera.capture'));
      await settle(t);
      expect(res.value!.id, 'camera.capture');
    });
  });

  // ------------------------------------------------------------------ editor

  group('editor', () {
    Future<Result<bool>> openEditor(
      WidgetTester t,
      FakeSchedulesApi api, {
      Schedule? existing,
      double scale = 1.0,
    }) async {
      final res = Result<bool>();
      await pumpHud(
        t,
        Scaffold(
          body: Builder(
            builder: (ctx) => Center(
              child: ElevatedButton(
                onPressed: () async {
                  res.value = await showDialog<bool>(
                    context: ctx,
                    builder: (_) => Dialog(
                      insetPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 24),
                      child: ScheduleEditorSheet(
                        service: api,
                        profileService: FakeProfileService(),
                        currentDeviceId: 'pixel-8',
                        existing: existing,
                      ),
                    ),
                  );
                  res.done = true;
                },
                child: const Text('open'),
              ),
            ),
          ),
        ),
        size: const Size(420, 900),
        textScale: scale,
      );
      await t.tap(find.text('open'));
      await settle(t);
      return res;
    }

    Future<void> choosePlugin(WidgetTester t) async {
      await t.tap(find.text('Plugin action'));
      await settle(t);
    }

    Future<void> pick(WidgetTester t, String id) async {
      final field = find.text('Choose an action…');
      await t.ensureVisible(field);
      await t.tap(field);
      await settle(t);
      await t.tap(find.text(id));
      await settle(t);
    }

    Finder field(String label) => find.widgetWithText(TextField, label);

    testWidgets('reminder flow is unchanged: creates the classic notification reminder', (t) async {
      final api = FakeSchedulesApi();
      final res = await openEditor(t, api);
      expect(find.text('New reminder'), findsOneWidget);
      await t.enterText(field('Title'), 'Water the plants');
      await t.tap(btn('Create reminder'));
      await settle(t);
      expect(api.created, hasLength(1));
      final c = api.created.single;
      expect(c['capability'], kReminderCapability);
      expect(c['parameters'], isNull); // service stamps {title, body}
      expect(c['title'], 'Water the plants');
      expect(c['kind'], 'one_shot');
      expect(res.done, isTrue);
      expect(res.value, isTrue);
    });

    testWidgets('a reminder needs a title', (t) async {
      final api = FakeSchedulesApi();
      await openEditor(t, api);
      await t.tap(btn('Create reminder'));
      await settle(t);
      expect(find.text('Title is required'), findsOneWidget);
      expect(api.created, isEmpty);
    });

    testWidgets('plugin action: picker, auto-selected device, generated form, summary, notice', (t) async {
      final api = FakeSchedulesApi();
      await openEditor(t, api);
      await choosePlugin(t);
      expect(find.text('New plugin action'), findsOneWidget);
      await pick(t, 'camera.capture');

      // single device -> chosen for the user
      expect(find.text('Pixel 8 (this device)'), findsOneWidget);
      // generated parameter form
      expect(field('Name *'), findsOneWidget);
      expect(find.text('Flash'), findsOneWidget);
      expect(find.textContaining('File name'), findsOneWidget);
      // calm notice; approval is only possible after saving
      expect(find.text(kConsentNeededMessage), findsOneWidget);
      expect(find.text('You can approve it right after saving.'), findsOneWidget);
      expect(btn('Approve…'), findsNothing);

      await t.enterText(field('Name *'), 'sunrise');
      await settle(t);
      expect(find.byKey(const Key('editor-summary')), findsOneWidget);
      expect(t.widget<Text>(find.byKey(const Key('editor-summary'))).data,
          'Runs camera.capture on Pixel 8 (this device) once on Nov 23, 13:00.');
    });

    testWidgets('invalid parameters are flagged inline and nothing is sent', (t) async {
      final api = FakeSchedulesApi();
      await openEditor(t, api);
      await choosePlugin(t);
      await t.enterText(find.byType(TextField).first, 'Morning photo');
      await pick(t, 'camera.capture');
      await t.ensureVisible(btn('Create action'));
      await t.tap(btn('Create action'));
      await settle(t);
      expect(find.text('Required'), findsOneWidget); // Name is required
      expect(api.created, isEmpty);

      await t.enterText(field('Name *'), 'x'); // too short (min 2)
      await t.tap(btn('Create action'));
      await settle(t);
      expect(find.text('At least 2 characters'), findsOneWidget);
      expect(api.created, isEmpty);
    });

    testWidgets('title, action and device are required', (t) async {
      final api = FakeSchedulesApi();
      await openEditor(t, api);
      await choosePlugin(t);
      await t.ensureVisible(btn('Create action'));
      await t.tap(btn('Create action'));
      await settle(t);
      expect(find.text('Title is required'), findsOneWidget);
      expect(find.text('Choose an action'), findsOneWidget);
      expect(api.created, isEmpty);
    });

    testWidgets('several devices: the user must choose, nothing is pre-selected', (t) async {
      final api = FakeSchedulesApi();
      await openEditor(t, api);
      await choosePlugin(t);
      await t.enterText(find.byType(TextField).first, 'Read log');
      await pick(t, 'files.read');
      await t.enterText(field('Path *'), '/tmp/log.txt');
      await t.ensureVisible(btn('Create action'));
      await t.tap(btn('Create action'));
      await settle(t);
      expect(find.text('Choose the device to run it on'), findsOneWidget);
      expect(api.created, isEmpty);
    });

    testWidgets('server 422 errors land on the matching inputs; unknown ones in a banner', (t) async {
      final api = FakeSchedulesApi()
        ..createError = const ScheduleValidationException([
          FieldError('parameters.name', 'name is already used by another schedule'),
          FieldError('title', 'Title is too generic'),
          FieldError('brand_new_field', 'Something unexpected'),
        ]);
      await openEditor(t, api);
      await choosePlugin(t);
      await t.enterText(find.byType(TextField).first, 'Photo');
      await pick(t, 'camera.capture');
      await t.enterText(field('Name *'), 'sunrise');
      await t.ensureVisible(btn('Create action'));
      await t.tap(btn('Create action'));
      await settle(t);
      expect(api.created, hasLength(1)); // client validation passed; the server said no
      expect(find.text('name is already used by another schedule'), findsOneWidget);
      expect(find.text('Title is too generic'), findsOneWidget);
      expect(find.text('brand_new_field: Something unexpected'), findsOneWidget);
    });

    testWidgets('saving never approves: the explicit Approve flow follows, and only it approves', (t) async {
      final api = FakeSchedulesApi();
      final res = await openEditor(t, api);
      await choosePlugin(t);
      await t.enterText(find.byType(TextField).first, 'Morning photo');
      await pick(t, 'camera.capture');
      await t.enterText(field('Name *'), 'sunrise');
      await t.ensureVisible(btn('Create action'));
      await t.tap(btn('Create action'));
      await settle(t);

      expect(api.created, hasLength(1));
      expect(api.created.single['parameters'], {'name': 'sunrise', 'flash': false});
      expect(api.created.single['targetDeviceId'], 'pixel-8');
      expect(api.approved, isEmpty, reason: 'saving must not approve');
      expect(res.done, isFalse); // stays open, offering approval
      expect(find.text('Saved'), findsOneWidget);
      expect(find.text(kConsentNeededMessage), findsOneWidget);

      await t.tap(btn('Approve…'));
      await settle(t);
      expect(find.text('Approve this to run on its own?'), findsOneWidget);
      expect(api.approved, isEmpty, reason: 'the dialog alone approves nothing');
      await t.tap(find.widgetWithText(FilledButton, 'Approve'));
      await settle(t);

      expect(api.approved, [('new', 30)]);
      expect(res.done, isTrue);
      expect(res.value, isTrue);
    });

    testWidgets('"Not now" leaves it saved but unapproved', (t) async {
      final api = FakeSchedulesApi();
      final res = await openEditor(t, api);
      await choosePlugin(t);
      await t.enterText(find.byType(TextField).first, 'Morning photo');
      await pick(t, 'camera.capture');
      await t.enterText(field('Name *'), 'sunrise');
      await t.ensureVisible(btn('Create action'));
      await t.tap(btn('Create action'));
      await settle(t);
      await t.tap(btn('Not now'));
      await settle(t);
      expect(api.approved, isEmpty);
      expect(res.value, isTrue); // list refreshes
    });

    testWidgets('an action that needs no approval just saves and closes', (t) async {
      final api = FakeSchedulesApi()
        ..onCreate = (p) => makeSchedule(id: 'n', capability: 'audio.play', requiresConsent: false, device: 'desk-linux');
      final res = await openEditor(t, api);
      await choosePlugin(t);
      await t.enterText(find.byType(TextField).first, 'Chime');
      await pick(t, 'audio.play');
      expect(find.text(kConsentNeededMessage), findsNothing);
      await t.ensureVisible(btn('Create action'));
      await t.tap(btn('Create action'));
      await settle(t);
      expect(res.done, isTrue);
      expect(api.created.single['parameters'], {'volume': 50}); // default shown & sent
    });

    testWidgets('loading the actions can fail and be retried', (t) async {
      final api = FakeSchedulesApi()..capsError = Exception('backend down');
      await openEditor(t, api);
      await choosePlugin(t);
      expect(find.text('Could not load the available actions.'), findsOneWidget);
      api.capsError = null;
      await t.tap(btn('Retry'));
      await settle(t);
      expect(find.text('Choose an action…'), findsOneWidget);
    });

    // ------------------------------------------------------------- edit mode

    Schedule approved() => makeSchedule(
          id: 's9', consent: approvedUntil(DateTime(2026, 12, 20)), parameters: const {'name': 'sunrise', 'flash': false});

    testWidgets('edit: shows the approval state and can revoke it', (t) async {
      final api = FakeSchedulesApi(items: [approved()]);
      await openEditor(t, api, existing: approved());
      expect(find.text('Edit plugin action'), findsOneWidget);
      expect(chip('Approved until Dec 20, 2026'), findsOneWidget);
      expect(find.text('Changing the action, its parameters or the device resets this approval.'),
          findsOneWidget);
      // existing values are prefilled
      expect(t.widget<TextField>(field('Name *')).controller!.text, 'sunrise');

      await t.tap(find.text('Revoke'));
      await settle(t);
      expect(api.revoked, ['s9']);
      expect(chip('Needs approval'), findsOneWidget);
      expect(find.text(kConsentNeededMessage), findsOneWidget);
    });

    testWidgets('edit: changing the action warns first, then reports that approval was reset', (t) async {
      final api = FakeSchedulesApi(items: [approved()])
        ..onUpdate = (id, p) => makeSchedule(id: id, parameters: p['parameters'] as Map<String, dynamic>); // consent gone
      await openEditor(t, api, existing: approved());
      await t.enterText(field('Name *'), 'sunset');
      await settle(t);
      expect(find.text('Saving these changes will reset your approval.'), findsOneWidget);

      await t.ensureVisible(btn('Save changes'));
      await t.tap(btn('Save changes'));
      await settle(t);

      // only what changed is sent (these are what reset approval server-side)
      expect(api.updated, hasLength(1));
      final u = api.updated.single;
      expect(u['parameters'], {'name': 'sunset', 'flash': false});
      expect(u['capability'], isNull);
      expect(u['targetDeviceId'], isNull);
      // and the user is told
      expect(find.text(kConsentResetMessage), findsOneWidget);
      expect(find.text(kConsentNeededMessage), findsOneWidget);
      expect(api.approved, isEmpty);
    });

    testWidgets('edit: a time-only change does not touch the action or approval', (t) async {
      final api = FakeSchedulesApi(items: [approved()])..onUpdate = (id, p) => approved();
      final res = await openEditor(t, api, existing: approved());
      await t.enterText(find.byType(TextField).first, 'Morning photo v2'); // title only
      await t.ensureVisible(btn('Save changes'));
      await t.tap(btn('Save changes'));
      await settle(t);
      final u = api.updated.single;
      expect(u['title'], 'Morning photo v2');
      expect(u['capability'], isNull);
      expect(u['parameters'], isNull);
      expect(u['targetDeviceId'], isNull);
      expect(res.done, isTrue); // still approved -> simply closes
    });

    testWidgets('edit: approving is only possible for the SAVED action', (t) async {
      final waiting = makeSchedule(id: 's5'); // needs approval
      final api = FakeSchedulesApi(items: [waiting]);
      await openEditor(t, api, existing: waiting);
      expect(find.text('Save your changes first — approval covers the saved action.'), findsNothing);
      expect(t.widget<GlowButton>(find.widgetWithText(GlowButton, 'Approve…'.toUpperCase())).onPressed, isNotNull);

      await t.enterText(field('Name *'), 'something else'); // unsaved change
      await settle(t);
      expect(find.text('Save your changes first — approval covers the saved action.'), findsOneWidget);
      expect(t.widget<GlowButton>(find.widgetWithText(GlowButton, 'Approve…'.toUpperCase())).onPressed, isNull);
    });

    testWidgets('edit: approving from the editor goes through the dialog and the chosen duration', (t) async {
      final waiting = makeSchedule(id: 's5');
      final api = FakeSchedulesApi(items: [waiting]);
      await openEditor(t, api, existing: waiting);
      await t.ensureVisible(btn('Approve…'));
      await t.tap(btn('Approve…'));
      await settle(t);
      await t.tap(find.text('7 days'));
      await settle(t);
      await t.tap(find.widgetWithText(FilledButton, 'Approve'));
      await settle(t);
      expect(api.approved, [('s5', 7)]);
      expect(chip('Approved until Nov 30, 2026'), findsOneWidget);
    });

    testWidgets('edit: an action that disappeared is shown as unavailable', (t) async {
      final gone = makeSchedule(id: 's7', capability: 'old.removed', parameters: const {});
      final api = FakeSchedulesApi(items: [gone]);
      await openEditor(t, api, existing: gone);
      expect(find.text('old.removed'), findsOneWidget);
      expect(find.text('This action is no longer available.'), findsOneWidget);
    });

    testWidgets('text scale 1.3 does not overflow the editor', (t) async {
      final api = FakeSchedulesApi();
      await openEditor(t, api, scale: 1.3);
      await choosePlugin(t);
      await pick(t, 'camera.capture');
      expect(t.takeException(), isNull);
      // the action selector and its buttons are real 48dp targets
      expect(t.getSize(find.byType(SegmentedButton<ActionKind>)).height, greaterThanOrEqualTo(48));
    });
  });
}
