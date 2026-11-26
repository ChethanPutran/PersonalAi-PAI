import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:pai_mobile/models/device.dart';
import 'package:pai_mobile/profile/profile_service.dart';

import '../../models/schedule.dart';
import '../../models/schedule_capability.dart';
import '../../schedules/param_form.dart';
import '../../schedules/schedule_service.dart';
import '../../schedules/schedule_timing.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';
import 'capability_picker.dart';
import 'consent_widgets.dart';
import 'param_form_fields.dart';

/// What the schedule does when it fires.
enum ActionKind { reminder, plugin }

/// Sheet for creating or editing a schedule — a notification **reminder**, or a **plugin
/// action** (a capability run on a device on a schedule).
///
/// Plugin actions that need approval to run unattended are never approved implicitly: saving
/// only stores the schedule; approval is the explicit "Approve…" button + confirmation dialog.
class ScheduleEditorSheet extends StatefulWidget {
  final ScheduleService service;
  final ProfileService? profileService;
  final String? currentDeviceId;

  /// Edit this schedule instead of creating a new one.
  final Schedule? existing;

  const ScheduleEditorSheet({
    super.key,
    required this.service,
    this.profileService,
    this.currentDeviceId,
    this.existing,
  });

  @override
  State<ScheduleEditorSheet> createState() => _ScheduleEditorSheetState();
}

class _ScheduleEditorSheetState extends State<ScheduleEditorSheet> {
  static const _knownFields = {
    'title', 'body', 'capability', 'target_device_id', 'cron', 'fire_at', 'interval_seconds',
    'kind', 'timezone', 'catch_up',
  };

  final _titleCtrl = TextEditingController();
  final _bodyCtrl = TextEditingController();
  final _cronCtrl = TextEditingController(text: '0 9 * * *');

  List<DeviceInfo> _devices = const [];
  bool _loadingDevices = false;
  String _timezone = 'UTC';

  // --- action ---
  late ActionKind _kind;
  String? _reminderDeviceId; // null = any online device (reminders)

  // plugin action
  List<SchedulableCapability> _caps = const [];
  bool _capsLoading = false;
  String? _capsError;
  SchedulableCapability? _capability;
  ParamFormController? _params;
  String? _pluginDeviceId;

  // --- timing ---
  late Timing _timing;

  // --- if the app was offline when it was due ---
  String _catchUp = 'once';
  int? _graceSeconds; // null = no limit

  // --- state ---
  Schedule? _current; // the stored schedule (edit mode; replaced by server responses)
  bool _dirty = false;
  bool _saving = false;
  bool _approving = false;
  bool _resetNote = false;
  Schedule? _saved; // set after a successful save that still needs approval
  Map<String, String> _fieldErrors = const {};
  List<String> _banner = const [];
  String? _error;

  bool get _editing => widget.existing != null;
  DateTime get _now => PaiClock.now();

  @override
  void initState() {
    super.initState();
    final s = widget.existing;
    _current = s;
    _kind = (s != null && !s.isReminder) ? ActionKind.plugin : ActionKind.reminder;
    _timing = s != null
        ? decodeTiming(s, now: _now)
        : Timing(repeat: RepeatChoice.once, fireAt: _now.add(const Duration(hours: 1)));
    if (s != null) {
      _titleCtrl.text = s.title;
      _bodyCtrl.text = s.body ?? '';
      _cronCtrl.text = _timing.cron;
      if (_kind == ActionKind.reminder) {
        _reminderDeviceId = s.targetDeviceId;
      } else {
        _pluginDeviceId = s.targetDeviceId;
      }
      _timezone = s.timezone;
      _catchUp = s.catchUp;
      _graceSeconds = s.misfireGraceSeconds;
    }
    _loadTimezone();
    _loadDevices();
    if (_kind == ActionKind.plugin) _loadCapabilities();
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _bodyCtrl.dispose();
    _cronCtrl.dispose();
    _params?.dispose();
    super.dispose();
  }

  // ------------------------------------------------------------------
  // Loading
  // ------------------------------------------------------------------

  Future<void> _loadDevices() async {
    final ps = widget.profileService;
    if (ps == null) return;
    setState(() => _loadingDevices = true);
    try {
      final profile = await ps.getProfile();
      final list = await ps.getDevices(userId: profile.id, currentDeviceId: widget.currentDeviceId);
      if (!mounted) return;
      setState(() {
        _devices = list;
        _loadingDevices = false;
      });
    } catch (e, st) {
      debugPrint('[ScheduleEditor] loading devices failed: $e');
      debugPrintStack(stackTrace: st, label: '[ScheduleEditor] devices', maxFrames: 5);
      if (!mounted) return;
      setState(() => _loadingDevices = false);
    }
  }

  Future<void> _loadTimezone() async {
    if (_editing) return; // keep the schedule's own timezone
    try {
      final dynamic result = await FlutterTimezone.getLocalTimezone();
      // flutter_timezone v1.x returns String, v2+/v3+ return TimezoneInfo.
      final id = result is String ? result : (result.identifier?.toString() ?? '');
      if (id.isNotEmpty && mounted) setState(() => _timezone = id);
    } catch (_) {
      // Keep UTC fallback — backend handles it.
    }
  }

  Future<void> _loadCapabilities() async {
    setState(() {
      _capsLoading = true;
      _capsError = null;
    });
    try {
      final all = await widget.service.getSchedulableCapabilities();
      if (!mounted) return;
      // The plain reminder has its own mode; the picker is for plugin actions only.
      final caps = all.where((c) => !c.isReminder).toList();
      setState(() {
        _caps = caps;
        _capsLoading = false;
      });
      final s = widget.existing;
      if (s != null && !s.isReminder && _capability == null) {
        final match = caps.where((c) => c.id == s.capability).firstOrNull;
        _selectCapability(match ?? SchedulableCapability(id: s.capability, schedulable: false,
            blockedReason: 'This action is no longer available.'),
            existingParams: s.parameters, keepDevice: true, markDirty: false);
      }
    } catch (e, st) {
      debugPrint('[ScheduleEditor] loading capabilities failed: $e');
      debugPrintStack(stackTrace: st, label: '[ScheduleEditor] capabilities', maxFrames: 5);
      if (!mounted) return;
      setState(() {
        _capsError = e.toString();
        _capsLoading = false;
      });
    }
  }

  // ------------------------------------------------------------------
  // Plugin-action selection
  // ------------------------------------------------------------------

  void _selectCapability(
    SchedulableCapability c, {
    Map<String, dynamic>? existingParams,
    bool keepDevice = false,
    bool markDirty = true,
  }) {
    _params?.dispose();
    final spec = ParamFormSpec.fromSchema(c.parametersSchema);
    final ctrl = ParamFormController(spec, existing: existingParams);
    // Rebuild on EVERY edit: the approval notes and the Approve button depend on the values.
    ctrl.onEdited = () {
      if (mounted) setState(() => _dirty = true);
    };
    setState(() {
      _capability = c;
      _params = ctrl;
      _fieldErrors = {..._fieldErrors}..remove('capability');
      if (!keepDevice) {
        // exactly one device can run it -> pick it; several -> the user must choose
        _pluginDeviceId = c.deviceIds.length == 1 ? c.deviceIds.single : null;
      }
      if (markDirty) _dirty = true;
    });
  }

  String _deviceName(String id) {
    final d = _devices.where((x) => x.id == id).firstOrNull;
    if (d == null) return id;
    return d.isCurrent ? '${d.name} (this device)' : d.name;
  }

  String get _deviceLabel {
    if (_kind == ActionKind.reminder) {
      return _reminderDeviceId == null ? 'any online device' : _deviceName(_reminderDeviceId!);
    }
    return _pluginDeviceId == null ? 'a device (not chosen yet)' : _deviceName(_pluginDeviceId!);
  }

  void _touch() => setState(() => _dirty = true);

  // ------------------------------------------------------------------
  // Save
  // ------------------------------------------------------------------

  Timing get _effectiveTiming => _timing.copyWith(cron: _cronCtrl.text);

  /// Parameters as they would be saved right now (null if the form is invalid).
  Map<String, dynamic>? _currentParams() {
    if (_kind == ActionKind.reminder) {
      final b = _bodyCtrl.text.trim();
      return {'title': _titleCtrl.text.trim(), 'body': b};
    }
    final v = _params?.valuesOrNull();
    return v == null ? null : Map<String, dynamic>.from(v);
  }

  /// The stored parameters as the form presents them (schema defaults filled in). "Unchanged" is
  /// judged against this, not against how an older client happened to store them — otherwise an
  /// untouched action would look edited and a save would needlessly reset the approval.
  Map<String, Object?> _baselineParams(Schedule s) {
    final spec = _params?.spec;
    if (spec == null) return s.parameters;
    final r = spec.collect(spec.initialRaw(s.parameters));
    return r.isValid ? r.values : s.parameters;
  }

  bool _paramsChanged(Map<String, dynamic>? current, Schedule s) =>
      current != null && _canon(current) != _canon(_baselineParams(s));

  /// Would saving change what runs (capability / parameters / device)? That is what makes the
  /// server drop an existing approval.
  bool _actionChangedNow() {
    final s = widget.existing;
    if (s == null || _kind == ActionKind.reminder || _capability == null) return false;
    return _capability!.id != s.capability ||
        _pluginDeviceId != s.targetDeviceId ||
        _paramsChanged(_currentParams(), s);
  }

  static String _canon(Object? v) => jsonEncode(_sorted(v));
  static Object? _sorted(Object? v) {
    if (v is Map) {
      final keys = v.keys.map((k) => k.toString()).toList()..sort();
      return {for (final k in keys) k: _sorted(v[k])};
    }
    if (v is List) return v.map(_sorted).toList();
    return v;
  }

  Future<void> _save() async {
    setState(() {
      _error = null;
      _banner = const [];
      _fieldErrors = const {};
    });

    final title = _titleCtrl.text.trim();
    final errors = <String, String>{};
    if (title.isEmpty) errors['title'] = 'Title is required';

    Map<String, dynamic>? params;
    if (_kind == ActionKind.plugin) {
      if (_capability == null) {
        errors['capability'] = 'Choose an action';
      } else if (!_capability!.schedulable) {
        errors['capability'] = _capability!.blockedReason ?? 'This action cannot be scheduled';
      }
      if (_capability != null) {
        if (_capability!.deviceIds.isEmpty) {
          errors['target_device_id'] = 'No device currently provides this action';
        } else if (_pluginDeviceId == null) {
          errors['target_device_id'] = 'Choose the device to run it on';
        }
      }
      final r = _params?.validate();
      if (r != null && !r.isValid) {
        // field errors are shown by the form itself
      } else if (r != null) {
        params = Map<String, dynamic>.from(r.values);
      }
      if (errors.isNotEmpty || (r != null && !r.isValid)) {
        setState(() => _fieldErrors = errors);
        return;
      }
    } else if (errors.isNotEmpty) {
      setState(() => _fieldErrors = errors); // shown under the field itself
      return;
    }

    setState(() => _saving = true);
    final enc = encodeTiming(_effectiveTiming);
    final body = _bodyCtrl.text.trim().isEmpty ? null : _bodyCtrl.text.trim();
    try {
      final Schedule saved;
      if (_editing) {
        final s = widget.existing!;
        final changed = _actionChangedNow();
        saved = await widget.service.update(
          s.id,
          title: title,
          body: body,
          kind: enc.kind,
          fireAt: enc.fireAt,
          cron: enc.cron,
          intervalSeconds: enc.intervalSeconds,
          timezone: _timezone,
          catchUp: _catchUp != s.catchUp ? _catchUp : null,
          misfireGraceSeconds: _graceSeconds != s.misfireGraceSeconds ? _graceSeconds : null,
          clearMisfireGrace: _graceSeconds == null && s.misfireGraceSeconds != null,
          // Only send what actually changed: these three reset an existing approval.
          capability: (_kind == ActionKind.plugin && _capability!.id != s.capability) ? _capability!.id : null,
          parameters: _kind == ActionKind.plugin
              ? ((_capability!.id != s.capability || _paramsChanged(params, s)) ? params : null)
              : {'title': title, 'body': body ?? ''},
          targetDeviceId: _kind == ActionKind.plugin
              ? (_pluginDeviceId != s.targetDeviceId ? _pluginDeviceId : null)
              : (_reminderDeviceId != s.targetDeviceId ? _reminderDeviceId : null),
        );
        if (changed && s.consent.active && !saved.consent.active) _resetNote = true;
      } else {
        saved = await widget.service.create(
          title: title,
          body: body,
          kind: enc.kind,
          fireAt: enc.fireAt,
          cron: enc.cron,
          intervalSeconds: enc.intervalSeconds,
          timezone: _timezone,
          catchUp: _catchUp,
          misfireGraceSeconds: _graceSeconds,
          capability: _kind == ActionKind.plugin ? _capability!.id : kReminderCapability,
          parameters: _kind == ActionKind.plugin ? params : null,
          targetDeviceId: _kind == ActionKind.plugin ? _pluginDeviceId : _reminderDeviceId,
        );
      }
      if (!mounted) return;
      final needsApproval = saved.consentStatus(_now).blocksRunning;
      if (needsApproval || _resetNote) {
        // Stay open: the user decides about approval explicitly, right here or later.
        setState(() {
          _saved = saved;
          _current = saved;
          _saving = false;
          _dirty = false;
        });
        return;
      }
      Navigator.of(context).pop(true);
    } on ScheduleValidationException catch (e) {
      if (!mounted) return;
      _params?.setServerErrors(e.parameterErrors);
      final mapped = <String, String>{
        for (final f in _knownFields)
          if (e.forField(f).isNotEmpty) f: e.forField(f).first,
      };
      setState(() {
        _fieldErrors = mapped;
        _banner = e.unmapped(_knownFields);
        _saving = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _saving = false;
      });
    }
  }

  // ------------------------------------------------------------------
  // Consent
  // ------------------------------------------------------------------

  Future<void> _approve() async {
    final s = _current;
    if (s == null) return;
    final days = await showApproveConsentDialog(
      context,
      ApprovalRequest.fromSchedule(s, deviceLabel: s.targetDeviceId == null ? 'any online device' : _deviceName(s.targetDeviceId!)),
    );
    if (days == null || !mounted) return; // cancelled: nothing is approved
    setState(() => _approving = true);
    try {
      final updated = await widget.service.approve(s.id, days: days);
      if (!mounted) return;
      if (_saved != null) {
        Navigator.of(context).pop(true);
        return;
      }
      setState(() {
        _current = updated;
        _approving = false;
        _resetNote = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Approval failed: $e';
        _approving = false;
      });
    }
  }

  Future<void> _revoke() async {
    final s = _current;
    if (s == null) return;
    setState(() => _approving = true);
    try {
      final updated = await widget.service.revoke(s.id);
      if (!mounted) return;
      setState(() {
        _current = updated;
        _approving = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Revoke failed: $e';
        _approving = false;
      });
    }
  }

  // ------------------------------------------------------------------
  // Pickers
  // ------------------------------------------------------------------

  Future<void> _pickDateTime() async {
    final d = await showDatePicker(
      context: context,
      initialDate: _timing.fireAt.isBefore(_now) ? _now : _timing.fireAt,
      firstDate: _now,
      lastDate: _now.add(const Duration(days: 365 * 5)),
    );
    if (d == null || !mounted) return;
    final t = await showTimePicker(context: context, initialTime: TimeOfDay.fromDateTime(_timing.fireAt));
    if (t == null) return;
    setState(() {
      _timing = _timing.copyWith(fireAt: DateTime(d.year, d.month, d.day, t.hour, t.minute));
      _dirty = true;
    });
  }

  // ------------------------------------------------------------------
  // Build
  // ------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final saved = _saved;
    final heading = saved != null
        ? 'Saved'
        : _editing
            ? (_kind == ActionKind.plugin ? 'Edit plugin action' : 'Edit reminder')
            : (_kind == ActionKind.plugin ? 'New plugin action' : 'New reminder');

    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(child: Text(heading, style: theme.textTheme.titleLarge)),
                  IconButton(
                    tooltip: 'Close',
                    icon: const Icon(Icons.close),
                    onPressed: () => Navigator.pop(context, saved != null ? true : null),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              if (saved != null) ..._savedView(context, saved) else ..._formView(context),
            ],
          ),
        ),
      ),
    );
  }

  // --- after saving something that still needs approval ---------------

  List<Widget> _savedView(BuildContext context, Schedule saved) {
    final status = saved.consentStatus(_now);
    return [
      Text('"${saved.title}" is saved.', style: Theme.of(context).textTheme.bodyLarge),
      const SizedBox(height: PaiSpacing.xs),
      Text('${saved.capability} · ${saved.repeatLabel}',
          style: PaiText.mono(size: 12, color: context.pai.muted)),
      const SizedBox(height: PaiSpacing.md),
      if (_resetNote)
        Padding(
          padding: const EdgeInsets.only(bottom: PaiSpacing.sm),
          child: Text(kConsentResetMessage, key: const Key('consent-reset-note'),
              style: TextStyle(color: context.pai.amber)),
        ),
      ConsentBanner(
        detail: status == ConsentStatus.expired
            ? 'The previous approval has expired.'
            : 'Until you approve it, it will be skipped instead of running.',
        onApprove: _approve,
        busy: _approving,
      ),
      if (_error != null) _errorText(context, _error!),
      const SizedBox(height: PaiSpacing.lg),
      GlowButton(
        label: 'Not now',
        variant: GlowVariant.outline,
        expanded: true,
        onPressed: () => Navigator.pop(context, true),
      ),
    ];
  }

  Widget _errorText(BuildContext context, String msg) => Padding(
        padding: const EdgeInsets.only(top: 12),
        child: Text(msg, style: TextStyle(color: Theme.of(context).colorScheme.error)),
      );

  // --- the form --------------------------------------------------------

  List<Widget> _formView(BuildContext context) {
    final theme = Theme.of(context);
    final plugin = _kind == ActionKind.plugin;
    final needsDateTime = _timing.repeat == RepeatChoice.once ||
        (_timing.repeat != RepeatChoice.customCron &&
            _timing.repeat != RepeatChoice.hourly &&
            _timing.repeat != RepeatChoice.keepInterval);

    return [
      // 1. Action selector
      SegmentedButton<ActionKind>(
        showSelectedIcon: false,
        style: const ButtonStyle(minimumSize: WidgetStatePropertyAll(Size(0, 48))),
        segments: const [
          ButtonSegment(value: ActionKind.reminder, icon: Icon(Icons.alarm), label: Text('Reminder')),
          ButtonSegment(value: ActionKind.plugin, icon: Icon(Icons.bolt), label: Text('Plugin action')),
        ],
        selected: {_kind},
        onSelectionChanged: _editing
            ? null
            : (s) {
                setState(() {
                  _kind = s.first;
                  _fieldErrors = const {};
                  _banner = const [];
                });
                if (_kind == ActionKind.plugin && _caps.isEmpty && !_capsLoading) _loadCapabilities();
              },
      ),
      const SizedBox(height: PaiSpacing.lg),

      // 2. Title / details
      HudTextField(
        controller: _titleCtrl,
        label: 'Title',
        hint: plugin ? 'Morning photo' : 'Water the plants',
        autofocus: !plugin && !_editing,
        errorText: _fieldErrors['title'],
        onChanged: (_) => _touch(),
      ),
      const SizedBox(height: 12),
      HudTextField(
        controller: _bodyCtrl,
        label: plugin ? 'Notes (optional)' : 'Details (optional)',
        maxLines: 2,
        errorText: _fieldErrors['body'],
        onChanged: (_) => _touch(),
      ),
      const SizedBox(height: 16),

      // 3. Plugin action: capability, device, parameters, consent
      if (plugin) ..._pluginSection(context),

      // 4. Timing
      DropdownButtonFormField<RepeatChoice>(
        key: ValueKey('repeat-${_timing.repeat}'),
        initialValue: _timing.repeat,
        decoration: const InputDecoration(labelText: 'Repeat'),
        items: [
          const DropdownMenuItem(value: RepeatChoice.once, child: Text('Once')),
          const DropdownMenuItem(value: RepeatChoice.hourly, child: Text('Every hour')),
          if (_timing.repeat == RepeatChoice.keepInterval)
            DropdownMenuItem(
                value: RepeatChoice.keepInterval,
                child: Text('Every ${_timing.intervalSeconds}s (unchanged)')),
          const DropdownMenuItem(value: RepeatChoice.daily, child: Text('Daily')),
          const DropdownMenuItem(value: RepeatChoice.weekly, child: Text('Weekly')),
          const DropdownMenuItem(value: RepeatChoice.monthly, child: Text('Monthly')),
          const DropdownMenuItem(value: RepeatChoice.yearly, child: Text('Yearly')),
          const DropdownMenuItem(value: RepeatChoice.customCron, child: Text('Custom cron…')),
        ],
        onChanged: (v) {
          if (v != null) {
            setState(() {
              _timing = _timing.copyWith(repeat: v);
              _dirty = true;
            });
          }
        },
      ),

      ..._missedRunSection(),

      if (!plugin && widget.profileService != null) ...[
        const SizedBox(height: 12),
        DropdownButtonFormField<String?>(
          key: ValueKey('reminder-device-$_reminderDeviceId-${_devices.length}'),
          initialValue: _reminderDeviceId,
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Run on'),
          items: [
            const DropdownMenuItem<String?>(value: null, child: Text('Any online device')),
            ..._devices.map(
              (d) => DropdownMenuItem<String?>(
                value: d.id,
                child: Row(
                  children: [
                    Icon(d.isCurrent ? Icons.smartphone : Icons.devices_other, size: 16),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(d.isCurrent ? '${d.name} (this device)' : d.name,
                          overflow: TextOverflow.ellipsis),
                    ),
                    if (d.connected) const Icon(Icons.circle, size: 8, color: PaiColors.green),
                  ],
                ),
              ),
            ),
          ],
          onChanged: _loadingDevices
              ? null
              : (v) => setState(() {
                    _reminderDeviceId = v;
                    _dirty = true;
                  }),
        ),
      ],
      const SizedBox(height: 12),
      if (needsDateTime)
        ListTile(
          contentPadding: EdgeInsets.zero,
          minVerticalPadding: 12,
          leading: const Icon(Icons.schedule),
          title: Text(_timing.repeat == RepeatChoice.once ? 'Fire at' : 'Time of day (start)'),
          subtitle: Text('${_timing.fireAt.toLocal()}'.split('.').first),
          trailing: const Icon(Icons.edit),
          onTap: _pickDateTime,
        ),
      if (_timing.repeat == RepeatChoice.customCron)
        HudTextField(
          controller: _cronCtrl,
          label: 'Cron expression',
          hint: '0 9 * * *',
          errorText: _fieldErrors['cron'],
          onChanged: (_) => _touch(),
        ),
      if (_fieldErrors['fire_at'] != null) _errorText(context, _fieldErrors['fire_at']!),
      if (_fieldErrors['interval_seconds'] != null) _errorText(context, _fieldErrors['interval_seconds']!),

      // 5. Summary
      if (plugin && _capability != null) ...[
        const SizedBox(height: PaiSpacing.sm),
        Semantics(
          container: true,
          child: Text(
            'Runs ${_capability!.id} on $_deviceLabel ${describeTiming(_effectiveTiming)}.',
            key: const Key('editor-summary'),
            style: theme.textTheme.bodyMedium?.copyWith(color: context.pai.strong),
          ),
        ),
      ],

      // 6. Errors
      for (final b in _banner) _errorText(context, b),
      if (_error != null) _errorText(context, _error!),
      const SizedBox(height: 20),
      GlowButton(
        label: _editing ? 'Save changes' : (plugin ? 'Create action' : 'Create reminder'),
        expanded: true,
        loading: _saving,
        onPressed: _saving ? null : _save,
      ),
    ];
  }

  // --- missed runs (the app or device was off when it was due) --------------

  static const _graceChoices = <int?, String>{
    null: 'No limit',
    300: '5 minutes',
    3600: '1 hour',
    21600: '6 hours',
    86400: '1 day',
  };

  List<Widget> _missedRunSection() {
    // A custom value saved elsewhere (API) stays selectable instead of crashing the dropdown.
    final graceItems = {..._graceChoices};
    if (!graceItems.containsKey(_graceSeconds)) graceItems[_graceSeconds] = '${_graceSeconds}s';
    return [
      const SizedBox(height: 12),
      DropdownButtonFormField<String>(
        key: ValueKey('catchup-$_catchUp'),
        initialValue: _catchUp,
        decoration: const InputDecoration(labelText: 'If a run is missed'),
        items: const [
          DropdownMenuItem(value: 'once', child: Text('Run once when back')),
          DropdownMenuItem(value: 'all', child: Text('Run every missed time')),
          DropdownMenuItem(value: 'none', child: Text('Skip it')),
        ],
        onChanged: (v) {
          if (v != null) setState(() { _catchUp = v; _dirty = true; });
        },
      ),
      const SizedBox(height: 12),
      DropdownButtonFormField<int?>(
        key: ValueKey('grace-$_graceSeconds'),
        initialValue: _graceSeconds,
        decoration: const InputDecoration(
            labelText: 'Still run if no later than', helperText: 'Older missed runs are skipped'),
        items: [
          for (final e in graceItems.entries) DropdownMenuItem<int?>(value: e.key, child: Text(e.value)),
        ],
        onChanged: (v) => setState(() { _graceSeconds = v; _dirty = true; }),
      ),
    ];
  }

  // --- plugin action section ---------------------------------------------

  List<Widget> _pluginSection(BuildContext context) {
    final theme = Theme.of(context);
    final cap = _capability;
    final out = <Widget>[];

    if (_capsLoading) {
      out.add(const Padding(
        padding: EdgeInsets.symmetric(vertical: 16),
        child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
      ));
    } else if (_capsError != null) {
      out.add(HudPanel(
        accent: context.pai.red,
        brackets: false,
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Could not load the available actions.', style: theme.textTheme.titleSmall),
          const SizedBox(height: 4),
          Text(_capsError!, style: theme.textTheme.bodySmall?.copyWith(color: context.pai.muted)),
          const SizedBox(height: 8),
          Align(
              alignment: Alignment.centerRight,
              child: GlowButton(label: 'Retry', dense: true, onPressed: _loadCapabilities)),
        ]),
      ));
      out.add(const SizedBox(height: 16));
    } else if (_caps.isEmpty && cap == null) {
      out.add(Text('No plugin actions can be scheduled yet.',
          style: theme.textTheme.bodyMedium?.copyWith(color: context.pai.muted)));
      out.add(const SizedBox(height: 16));
    } else {
      out.add(CapabilityPickerField(
        capabilities: cap != null && !_caps.any((c) => c.id == cap.id) ? [..._caps, cap] : _caps,
        selectedId: cap?.id,
        errorText: _fieldErrors['capability'],
        onSelected: (c) => _selectCapability(c),
      ));
      out.add(const SizedBox(height: 12));
    }

    if (cap != null && !cap.schedulable) {
      out.add(Text(cap.blockedReason ?? 'This action cannot be scheduled.',
          style: TextStyle(color: context.pai.amber)));
      out.add(const SizedBox(height: 12));
    }

    if (cap != null) {
      // Device — only those that can run it
      if (cap.deviceIds.isEmpty) {
        out.add(Text('No device currently provides this action.',
            style: TextStyle(color: context.pai.amber)));
      } else if (cap.deviceIds.length == 1) {
        out.add(InputDecorator(
          decoration: InputDecoration(labelText: 'Run on', errorText: _fieldErrors['target_device_id']),
          child: Text(_deviceName(cap.deviceIds.single)),
        ));
      } else {
        out.add(DropdownButtonFormField<String>(
          key: ValueKey('plugin-device-$_pluginDeviceId-${cap.id}-${_devices.length}'),
          initialValue: cap.deviceIds.contains(_pluginDeviceId) ? _pluginDeviceId : null,
          isExpanded: true,
          decoration: InputDecoration(labelText: 'Run on', errorText: _fieldErrors['target_device_id']),
          items: [
            for (final id in cap.deviceIds) DropdownMenuItem(value: id, child: Text(_deviceName(id))),
          ],
          onChanged: (v) => setState(() {
            _pluginDeviceId = v;
            _dirty = true;
          }),
        ));
      }
      out.add(const SizedBox(height: 16));

      // Parameters
      if (_params != null && !_params!.spec.isEmpty) {
        out.add(Text('Parameters', style: theme.textTheme.titleSmall));
        out.add(const SizedBox(height: 8));
      }
      if (_params != null) out.add(ParamForm(controller: _params!));

      out.addAll(_consentSection(context, cap));
    }
    return out;
  }

  /// Approval notices inside the editor.
  List<Widget> _consentSection(BuildContext context, SchedulableCapability cap) {
    final s = _current;
    final out = <Widget>[];
    final theme = Theme.of(context);

    if (s == null) {
      // creating: approval is possible only after the schedule exists
      if (cap.requiresConsent) {
        out.add(ConsentBanner(detail: 'You can approve it right after saving.'));
        out.add(const SizedBox(height: 16));
      }
      return out;
    }

    final requires = s.requiresConsent || cap.requiresConsent;
    if (!requires) return out;
    final status = s.consentStatus(_now);
    final changed = _actionChangedNow();

    out.add(Row(children: [
      ConsentChip(status: status, expiresAt: s.consent.expiresAt, compact: false),
      const Spacer(),
      if (status == ConsentStatus.approved || status == ConsentStatus.expiringSoon)
        TextButton.icon(
          onPressed: _approving ? null : _revoke,
          icon: const Icon(Icons.block, size: 18),
          label: const Text('Revoke'),
        ),
    ]));
    out.add(const SizedBox(height: PaiSpacing.sm));

    if (status.blocksRunning) {
      out.add(ConsentBanner(
        detail: status == ConsentStatus.expired ? 'The previous approval has expired.' : null,
        onApprove: _approve,
        approveEnabled: !_dirty && !changed,
        disabledReason: 'Save your changes first — approval covers the saved action.',
        busy: _approving,
      ));
    } else if (changed) {
      out.add(Text('Saving these changes will reset your approval.',
          style: theme.textTheme.bodySmall?.copyWith(color: context.pai.amber)));
    } else {
      out.add(Text('Changing the action, its parameters or the device resets this approval.',
          style: theme.textTheme.bodySmall?.copyWith(color: context.pai.muted)));
    }
    out.add(const SizedBox(height: 16));
    return out;
  }
}
