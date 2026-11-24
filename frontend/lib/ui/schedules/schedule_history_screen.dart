import 'package:flutter/material.dart';

import '../../models/schedule.dart';
import '../../schedules/schedule_service.dart';
import '../../tasks/task.dart';
import '../../tasks/task_service.dart';
import '../tasks/task_detail_screen.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';
import 'consent_widgets.dart';

class ScheduleHistoryScreen extends StatefulWidget {
  final Schedule schedule;
  final ScheduleService scheduleService;
  final TaskService taskService;

  const ScheduleHistoryScreen({
    super.key,
    required this.schedule,
    required this.scheduleService,
    required this.taskService,
  });

  @override
  State<ScheduleHistoryScreen> createState() => _ScheduleHistoryScreenState();
}

class _ScheduleHistoryScreenState extends State<ScheduleHistoryScreen> {
  List<Task> _tasks = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final list = await widget.scheduleService.getHistory(widget.schedule.id);
      if (!mounted) return;
      setState(() {
        _tasks = list;
        _loading = false;
      });
    } catch (e, st) {
      debugPrint('[ScheduleHistory] load failed: ${e.runtimeType}: $e');
      debugPrintStack(stackTrace: st, label: '[ScheduleHistory] load', maxFrames: 8);
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return HudScaffold(
      appBar: HudAppBar(titleWidget: Text('History · ${widget.schedule.title}')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _buildBody(),
      ),
    );
  }

  /// Same vocabulary as the list card: what it runs, approval state, last outcome.
  Widget _summary(BuildContext context) {
    final sch = widget.schedule;
    final text = Theme.of(context).textTheme;
    final p = context.pai;
    final consent = sch.consentStatus(PaiClock.now());
    return HudPanel(
      brackets: false,
      padding: const EdgeInsets.all(PaiSpacing.md),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(spacing: PaiSpacing.sm, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center, children: [
          Icon(sch.isReminder ? Icons.alarm : Icons.bolt, size: 18, color: p.cyan),
          Text(sch.isReminder ? 'Reminder' : sch.capability,
              style: sch.isReminder ? text.titleSmall : PaiText.mono(size: 13, color: p.strong)),
          if (!sch.isReminder) RiskChip(sch.risk),
          if (consent != ConsentStatus.notRequired)
            ConsentChip(status: consent, expiresAt: sch.consent.expiresAt),
        ]),
        const SizedBox(height: 4),
        Text(sch.repeatLabel, style: text.bodySmall?.copyWith(color: p.muted)),
      ]),
    );
  }

  Widget _buildBody() {
    if (_loading) return const HudSkeletonList(count: 4);
    if (_error != null) {
      return LayoutBuilder(
        builder: (context, box) => ListView(children: [
          SizedBox(
            height: box.maxHeight,
            child: HudErrorState(title: 'Failed to load history', details: _error, onRetry: _load),
          ),
        ]),
      );
    }
    if (_tasks.isEmpty) {
      return ListView(
        padding: const EdgeInsets.all(PaiSpacing.lg),
        children: [
          _summary(context),
          const SizedBox(height: PaiSpacing.xl),
          const SizedBox(
            height: 260,
            child: HudEmptyState(
              icon: Icons.history,
              title: 'No firings yet',
              message: 'Each time this fires, its task will appear here.',
            ),
          ),
        ],
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.all(PaiSpacing.lg),
      itemCount: _tasks.length + 1,
      separatorBuilder: (_, _) => const SizedBox(height: PaiSpacing.sm),
      itemBuilder: (_, i) {
        if (i == 0) return _summary(context);
        final t = _tasks[i - 1];
        return HudPanel(
          brackets: false,
          padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.md, vertical: PaiSpacing.sm),
          semanticLabel: 'Run ${_fmt(t.createdAt)}, ${t.status.name}',
          onTap: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => TaskDetailScreen(initialTask: t, service: widget.taskService),
          )),
          child: Row(children: [
            Expanded(child: Text(_fmt(t.createdAt), style: PaiText.mono(size: 13, color: context.pai.strong))),
            StatusChip(status: HudStatusX.fromTaskStatus(t.status.name), compact: true),
            const SizedBox(width: PaiSpacing.sm),
            Icon(Icons.chevron_right, color: context.pai.muted),
          ]),
        );
      },
    );
  }

  String _fmt(DateTime d) {
    final l = d.toLocal();
    return '${l.year}-${l.month.toString().padLeft(2, '0')}-'
        '${l.day.toString().padLeft(2, '0')} '
        '${l.hour.toString().padLeft(2, '0')}:'
        '${l.minute.toString().padLeft(2, '0')}';
  }
}