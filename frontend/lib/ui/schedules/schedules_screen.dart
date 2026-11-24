import 'package:flutter/material.dart';
import 'package:pai_mobile/models/device.dart';
import 'package:pai_mobile/profile/profile_service.dart';
import 'package:pai_mobile/tasks/task_service.dart';
import 'package:pai_mobile/ui/schedules/schedule_history_screen.dart';

import '../../models/schedule.dart';
import '../../schedules/schedule_service.dart';
import 'consent_widgets.dart';
import 'schedule_card.dart';
import 'schedule_editor_sheet.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';

class SchedulesScreen extends StatefulWidget {
  final ScheduleService service;
  final TaskService taskService;
  final ProfileService? profileService;     
  final String? currentDeviceId;            

  /// Optional live feed of `schedule.*` events from the WS.
  final Stream<Map<String, dynamic>>? events;

  const SchedulesScreen({
    super.key,
    required this.service,
    required this.taskService,
    this.events,
    this.profileService,
    this.currentDeviceId,
  });

  @override
  State<SchedulesScreen> createState() => _SchedulesScreenState();
}

class _SchedulesScreenState extends State<SchedulesScreen> {
  List<Schedule> _schedules = const [];
  Map<String, String> _deviceNames = const {};
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadDeviceNames();
    _load();
    widget.events?.listen((msg) {
      final type = msg['type']?.toString() ?? '';
      if (type.startsWith('schedule.')) _load();
    });
  }

  /// Device names for the cards ("on Pixel 8"). Best-effort: ids are shown if this fails.
  Future<void> _loadDeviceNames() async {
    final ps = widget.profileService;
    if (ps == null) return;
    try {
      final profile = await ps.getProfile();
      final list = await ps.getDevices(userId: profile.id, currentDeviceId: widget.currentDeviceId);
      if (!mounted) return;
      setState(() => _deviceNames = {
            for (final DeviceInfo d in list) d.id: d.isCurrent ? '${d.name} (this device)' : d.name,
          });
    } catch (e, st) {
      debugPrint('[SchedulesScreen] loading device names failed: $e');
      debugPrintStack(stackTrace: st, label: '[SchedulesScreen] device names', maxFrames: 5);
    }
  }

  String _deviceLabel(Schedule s) {
    final id = s.targetDeviceId;
    if (id == null) return 'any online device';
    return _deviceNames[id] ?? id;
  }

  Future<void> _load() async {
    setState(() {
      _loading = _schedules.isEmpty;
      _error = null;
    });
    try {
      final list = await widget.service.list();
      if (!mounted) return;
      setState(() {
        _schedules = list;
        _loading = false;
      });
    } catch (e, st) {
      debugPrint('[SchedulesScreen] load failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[SchedulesScreen] load', maxFrames: 8);
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _openEditor({Schedule? existing}) async {
  await showDialog<bool>(
    context: context,
    barrierDismissible: true,
    builder: (_) => Dialog(
      insetPadding: const EdgeInsets.symmetric(horizontal: 24, vertical: 40),
      clipBehavior: Clip.antiAlias,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 520, maxHeight: 760),
        child: IntrinsicHeight(   // ← shrink-wraps on mobile, capped at 760
    child: ScheduleEditorSheet(
      service: widget.service,
      profileService: widget.profileService,
      currentDeviceId: widget.currentDeviceId,
      existing: existing,
    ),
  ),
      ),
    ),
  );
  // The editor may have saved, approved or revoked even if it was dismissed by tapping
  // outside, so always refresh.
  if (mounted) await _load();
}

  Future<void> _approve(Schedule s) async {
    final days = await showApproveConsentDialog(
      context,
      ApprovalRequest.fromSchedule(s, deviceLabel: _deviceLabel(s)),
    );
    if (days == null || !mounted) return; // cancelled: nothing is approved
    try {
      await widget.service.approve(s.id, days: days);
      await _load();
    } catch (e, st) {
      debugPrint('[SchedulesScreen] approve failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[SchedulesScreen] approve', maxFrames: 6);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Approval failed: $e')));
    }
  }

  Future<void> _revoke(Schedule s) async {
    try {
      await widget.service.revoke(s.id);
      await _load();
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Approval revoked')));
    } catch (e, st) {
      debugPrint('[SchedulesScreen] revoke failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[SchedulesScreen] revoke', maxFrames: 6);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Revoke failed: $e')));
    }
  }

  Future<void> _toggle(Schedule s) async {
    try {
      await widget.service.setEnabled(s.id, !s.enabled);
      await _load();
    } catch (e, st) {
      debugPrint('[SchedulesScreen] toggle failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[SchedulesScreen] toggle', maxFrames: 6);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Update failed: $e')));
    }
  }

  Future<void> _delete(Schedule s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(s.isReminder ? 'Delete reminder?' : 'Delete scheduled action?'),
        content: Text('"${s.title}" will be permanently removed.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(ctx).colorScheme.error,
            ),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.service.delete(s.id);
      await _load();
    } catch (e, st) {
      debugPrint('[SchedulesScreen] delete failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[SchedulesScreen] delete', maxFrames: 6);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Delete failed: $e')));
    }
  }

void _openHistory(Schedule s) {
  Navigator.of(context).push(MaterialPageRoute(
    builder: (_) => ScheduleHistoryScreen(
      schedule: s,
      scheduleService: widget.service,
      taskService: widget.taskService,
    ),
  ));
}
  // ------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return HudScaffold(
      appBar: HudAppBar(
        title: 'Reminders',
        automaticallyImplyLeading: false,
        actions: [
          HudIconButton(
            icon: Icons.refresh,
            tooltip: 'Refresh',
            onPressed: _loading ? null : _load,
          ),
          const SizedBox(width: 8),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _openEditor,
        icon: const Icon(Icons.add_alarm),
        label: const Text('New'),
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const HudSkeletonList(count: 3);
    }
    if (_error != null) {
      return LayoutBuilder(
        builder: (context, box) => ListView(
          children: [
            SizedBox(
              height: box.maxHeight,
              child: HudErrorState(
                title: 'Failed to load reminders',
                details: _error,
                onRetry: _load,
              ),
            ),
          ],
        ),
      );
    }
    if (_schedules.isEmpty) {
      return LayoutBuilder(
        builder: (context, box) => ListView(
          children: [
            SizedBox(
              height: box.maxHeight,
              child: const HudEmptyState(
                icon: Icons.alarm_off,
                title: 'No reminders yet',
                message: 'Tap "New" to add a reminder or schedule a plugin action.',
              ),
            ),
          ],
        ),
      );
    }

    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 860),
        child: ListView.separated(
          padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, PaiSpacing.lg, PaiSpacing.lg, 96),
          itemCount: _schedules.length,
          separatorBuilder: (_, _) => const SizedBox(height: PaiSpacing.md),
          itemBuilder: (_, i) {
            final s = _schedules[i];
            return ScheduleCard(
              schedule: s,
              deviceLabel: _deviceLabel(s),
              onToggle: () => _toggle(s),
              onApprove: () => _approve(s),
              onMenu: (action) {
                switch (action) {
                  case ScheduleMenuAction.history:
                    _openHistory(s);
                  case ScheduleMenuAction.edit:
                    _openEditor(existing: s);
                  case ScheduleMenuAction.toggle:
                    _toggle(s);
                  case ScheduleMenuAction.revoke:
                    _revoke(s);
                  case ScheduleMenuAction.delete:
                    _delete(s);
                }
              },
            );
          },
        ),
      ),
    );
  }
}
