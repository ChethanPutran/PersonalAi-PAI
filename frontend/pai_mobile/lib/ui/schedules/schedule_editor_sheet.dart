import 'package:flutter/material.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:pai_mobile/models/user_device.dart';
import 'package:pai_mobile/profile/profile_service.dart';

import '../../schedules/schedule_service.dart';

/// Modal bottom sheet for creating a schedule.
///
/// Repeat picker exposes the simple cases first (Once / Hourly / Daily /
/// Weekly / Monthly / Yearly / Custom cron). Internally everything ends
/// up as one of `one_shot`, `cron`, or `interval` to match the backend.
class ScheduleEditorSheet extends StatefulWidget {
  final ScheduleService service;
  final ProfileService? profileService;
  final String? currentDeviceId;

  const ScheduleEditorSheet({
    super.key,
    required this.service,
    this.profileService,
    this.currentDeviceId,
  });

  @override
  State<ScheduleEditorSheet> createState() => _ScheduleEditorSheetState();
}

enum _Repeat { once, hourly, daily, weekly, monthly, yearly, customCron }

class _ScheduleEditorSheetState extends State<ScheduleEditorSheet> {
  final _titleCtrl = TextEditingController();
  final _bodyCtrl = TextEditingController();
  final _cronCtrl = TextEditingController(text: '0 9 * * *');

  List<UserDevice> _devices = const [];
  String? _targetDeviceId; // null = any online device
  bool _loadingDevices = false;

  String _timezone = 'UTC';

  _Repeat _repeat = _Repeat.once;
  DateTime _fireAt = DateTime.now().add(const Duration(hours: 1));
  String? _saving;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadTimezone();
    _loadDevices();
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _bodyCtrl.dispose();
    _cronCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadDevices() async {
    final ps = widget.profileService;
    if (ps == null) return;
    setState(() => _loadingDevices = true);
    try {
      final profile = await ps.getProfile();
      final list = await ps.getDevices(
        userId: profile.id,
        currentDeviceId: widget.currentDeviceId,
      );
      if (!mounted) return;
      setState(() {
        _devices = list;
        _loadingDevices = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loadingDevices = false);
    }
  }

  Future<void> _loadTimezone() async {
    try {
      final dynamic result = await FlutterTimezone.getLocalTimezone();
      // flutter_timezone v1.x returns String, v2+/v3+ return TimezoneInfo.
      final id = result is String
          ? result
          : (result.identifier?.toString() ?? '');
      if (id.isNotEmpty && mounted) {
        setState(() => _timezone = id);
      }
    } catch (_) {
      // Keep UTC fallback — backend handles it.
    }
  }

  // ------------------------------------------------------------------
  // Submit
  // ------------------------------------------------------------------

  Future<void> _save() async {
    final title = _titleCtrl.text.trim();
    if (title.isEmpty) {
      setState(() => _error = 'Title is required');
      return;
    }

    setState(() {
      _saving = 'Saving…';
      _error = null;
    });

    try {
      final (kind, cron, interval, fireAt) = _encodeRepeat();

      await widget.service.create(
        title: title,
        body: _bodyCtrl.text.trim().isEmpty ? null : _bodyCtrl.text.trim(),
        kind: kind,
        fireAt: fireAt,
        cron: cron,
        intervalSeconds: interval,
        timezone: _localTimezone(),
        targetDeviceId: _targetDeviceId,
      );

      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _saving = null;
      });
    }
  }

  (String, String?, int?, DateTime?) _encodeRepeat() {
    switch (_repeat) {
      case _Repeat.once:
        return ('one_shot', null, null, _fireAt);
      case _Repeat.hourly:
        return ('interval', null, 3600, null);
      case _Repeat.daily:
        // Cron at hh:mm daily
        return ('cron', '${_fireAt.minute} ${_fireAt.hour} * * *', null, null);
      case _Repeat.weekly:
        // day-of-week 0=Sun … 6=Sat
        final dow = _fireAt.weekday % 7;
        return (
          'cron',
          '${_fireAt.minute} ${_fireAt.hour} * * $dow',
          null,
          null,
        );
      case _Repeat.monthly:
        return (
          'cron',
          '${_fireAt.minute} ${_fireAt.hour} ${_fireAt.day} * *',
          null,
          null,
        );
      case _Repeat.yearly:
        return (
          'cron',
          '${_fireAt.minute} ${_fireAt.hour} ${_fireAt.day} ${_fireAt.month} *',
          null,
          null,
        );
      case _Repeat.customCron:
        return ('cron', _cronCtrl.text.trim(), null, null);
    }
  }

  String _localTimezone() => _timezone;

  // ------------------------------------------------------------------
  // Pickers
  // ------------------------------------------------------------------

  Future<void> _pickDateTime() async {
    final d = await showDatePicker(
      context: context,
      initialDate: _fireAt,
      firstDate: DateTime.now(),
      lastDate: DateTime.now().add(const Duration(days: 365 * 5)),
    );
    if (d == null || !mounted) return;
    final t = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(_fireAt),
    );
    if (t == null) return;
    setState(() {
      _fireAt = DateTime(d.year, d.month, d.day, t.hour, t.minute);
    });
  }

  // ------------------------------------------------------------------
  // Build
  // ------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final needsDateTime =
        _repeat == _Repeat.once ||
        _repeat != _Repeat.customCron && _repeat != _Repeat.hourly;

    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Text('New reminder', style: theme.textTheme.titleLarge),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.close),
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              TextField(
                controller: _titleCtrl,
                autofocus: true,
                decoration: const InputDecoration(
                  labelText: 'Title',
                  hintText: 'Water the plants',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _bodyCtrl,
                maxLines: 2,
                decoration: const InputDecoration(
                  labelText: 'Details (optional)',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 16),
              DropdownButtonFormField<_Repeat>(
                value: _repeat,
                decoration: const InputDecoration(
                  labelText: 'Repeat',
                  border: OutlineInputBorder(),
                ),
                items: const [
                  DropdownMenuItem(value: _Repeat.once, child: Text('Once')),
                  DropdownMenuItem(
                    value: _Repeat.hourly,
                    child: Text('Every hour'),
                  ),
                  DropdownMenuItem(value: _Repeat.daily, child: Text('Daily')),
                  DropdownMenuItem(
                    value: _Repeat.weekly,
                    child: Text('Weekly'),
                  ),
                  DropdownMenuItem(
                    value: _Repeat.monthly,
                    child: Text('Monthly'),
                  ),
                  DropdownMenuItem(
                    value: _Repeat.yearly,
                    child: Text('Yearly'),
                  ),
                  DropdownMenuItem(
                    value: _Repeat.customCron,
                    child: Text('Custom cron…'),
                  ),
                ],
                onChanged: (v) {
                  if (v != null) setState(() => _repeat = v);
                },
              ),

              if (widget.profileService != null) ...[
                const SizedBox(height: 12),
                DropdownButtonFormField<String?>(
                  value: _targetDeviceId,
                  isExpanded: true,
                  decoration: const InputDecoration(
                    labelText: 'Run on',
                    border: OutlineInputBorder(),
                  ),
                  items: [
                    const DropdownMenuItem<String?>(
                      value: null,
                      child: Text('Any online device'),
                    ),
                    ..._devices.map(
                      (d) => DropdownMenuItem<String?>(
                        value: d.id,
                        child: Row(
                          children: [
                            Icon(
                              d.isCurrent
                                  ? Icons.smartphone
                                  : Icons.devices_other,
                              size: 16,
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                d.isCurrent
                                    ? '${d.name} (this device)'
                                    : d.name,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                            if (d.connected)
                              const Icon(
                                Icons.circle,
                                size: 8,
                                color: Colors.green,
                              ),
                          ],
                        ),
                      ),
                    ),
                  ],
                  onChanged: _loadingDevices
                      ? null
                      : (v) => setState(() => _targetDeviceId = v),
                ),
              ],
              const SizedBox(height: 12),
              if (needsDateTime)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.schedule),
                  title: Text(
                    _repeat == _Repeat.once ? 'Fire at' : 'Time of day (start)',
                  ),
                  subtitle: Text('${_fireAt.toLocal()}'.split('.').first),
                  trailing: const Icon(Icons.edit),
                  onTap: _pickDateTime,
                ),
              if (_repeat == _Repeat.customCron)
                TextField(
                  controller: _cronCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Cron expression',
                    hintText: '0 9 * * *',
                    helperText: 'min hour dom month dow',
                    border: OutlineInputBorder(),
                  ),
                ),
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
              ],
              const SizedBox(height: 20),
              FilledButton(
                onPressed: _saving == null ? _save : null,
                child: _saving != null
                    ? const SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Create reminder'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
