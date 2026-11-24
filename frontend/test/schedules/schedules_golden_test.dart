// Visual regression images for the scheduled-plugin-action UI. Regenerate with
//   flutter test test/schedules/schedules_golden_test.dart --update-goldens
// then LOOK at test/goldens/schedule_*.png.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/models/schedule.dart';
import 'package:pai_mobile/ui/schedules/consent_widgets.dart';
import 'package:pai_mobile/ui/schedules/schedule_editor_sheet.dart';
import 'package:pai_mobile/ui/schedules/schedules_screen.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import '../ui/fakes.dart';
import '../ui/golden_harness.dart';
import 'fakes.dart';

Future<void> settle(WidgetTester t) async {
  for (var i = 0; i < 6; i++) {
    await t.pump(const Duration(milliseconds: 60));
  }
}

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  Widget editor(FakeSchedulesApi api, {Schedule? existing}) => Scaffold(
        body: SafeArea(
          child: Align(
            alignment: Alignment.topCenter,
            child: ScheduleEditorSheet(
              service: api,
              profileService: FakeProfileService(),
              currentDeviceId: 'pixel-8',
              existing: existing,
            ),
          ),
        ),
      );

  testWidgets('editor: plugin action with generated form and the approval notice', (t) async {
    final api = FakeSchedulesApi();
    await pumpHud(t, editor(api), size: const Size(420, 1180));
    await settle(t);
    await t.tap(find.text('Plugin action'));
    await settle(t);
    await t.enterText(find.byType(TextField).first, 'Morning photo');
    await t.tap(find.text('Choose an action…'));
    await settle(t);
    await t.tap(find.text('camera.capture'));
    await settle(t);
    await t.enterText(find.widgetWithText(TextField, 'Name *'), 'sunrise');
    await settle(t);
    await expectGolden(t, 'schedule_editor_plugin');
  });

  testWidgets('editor: editing an action that is waiting for approval', (t) async {
    final waiting = makeSchedule(id: 's5', title: 'Morning photo');
    final api = FakeSchedulesApi(items: [waiting]);
    await pumpHud(t, editor(api, existing: waiting), size: const Size(420, 1180));
    await settle(t);
    await expectGolden(t, 'schedule_editor_needs_approval');
  });

  testWidgets('approve dialog states exactly what will run', (t) async {
    await pumpHud(
      t,
      Scaffold(
        body: Builder(
          builder: (ctx) => Center(
            child: ElevatedButton(
              onPressed: () => showApproveConsentDialog(
                ctx,
                const ApprovalRequest(
                  capability: 'camera.capture',
                  deviceLabel: 'Pixel 8 (this device)',
                  parameters: {'name': 'sunrise', 'flash': false},
                  scheduleLabel: 'Cron: 0 9 * * *',
                  risk: ScheduleRisk.medium,
                ),
              ),
              child: const Text('open'),
            ),
          ),
        ),
      ),
      size: const Size(420, 760),
    );
    await t.tap(find.text('open'));
    await settle(t);
    await expectGolden(t, 'schedule_approve_dialog');
  });

  testWidgets('list: reminders and plugin actions in every approval state', (t) async {
    final items = [
      makeSchedule(
        id: 'a', title: 'Call Mom', capability: kReminderCapability, requiresConsent: false,
        risk: ScheduleRisk.low, device: null, kind: 'one_shot', cron: null,
        fireAt: DateTime(2026, 11, 24, 18, 30), nextFireAt: DateTime(2026, 11, 24, 18, 30),
        parameters: const {},
      ),
      makeSchedule(
        id: 'b', title: 'Morning photo', consent: approvedUntil(DateTime(2026, 12, 20)),
        lastRunStatus: LastRunStatus.ok, lastRunAt: DateTime(2026, 11, 23, 9),
      ),
      makeSchedule(
        id: 'c', title: 'Read server log', capability: 'files.read', device: 'desk-linux',
        risk: ScheduleRisk.high, parameters: const {'path': '/var/log/syslog'},
        lastRunStatus: LastRunStatus.skippedNoConsent, lastRunAt: DateTime(2026, 11, 23, 9),
      ),
      makeSchedule(
        id: 'd', title: 'Hourly snapshot', kind: 'interval', cron: null, interval: 3600,
        consent: approvedUntil(DateTime(2026, 11, 25)),
        lastRunStatus: LastRunStatus.failed, lastRunAt: DateTime(2026, 11, 23, 11),
        lastRunError: 'Device pixel-8 did not respond in 60s',
      ),
    ];
    await pumpHud(
      t,
      SchedulesScreen(
        service: FakeSchedulesApi(items: items),
        taskService: FakeTaskService(const []),
        events: const Stream.empty(),
        profileService: FakeProfileService(),
        currentDeviceId: 'pixel-8',
      ),
      size: const Size(380, 1500),
    );
    await settle(t);
    await expectGolden(t, 'schedule_list_mixed');
  });
}
