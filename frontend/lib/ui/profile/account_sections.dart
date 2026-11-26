import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../profile/account_service.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';
import 'chip_input.dart';

// Three self-contained forms for the Profile screen. Each owns its controllers, tracks whether it
// differs from what the server has, and saves on its own button, so typing in one never rebuilds the
// screen or the other forms.

/// Save button + the last result, shared by the three forms.
class _SaveRow extends StatelessWidget {
  final bool dirty;
  final bool saving;
  final VoidCallback onSave;
  final String? message;
  final bool isError;
  final Key? buttonKey;
  const _SaveRow({
    required this.dirty,
    required this.saving,
    required this.onSave,
    required this.message,
    required this.isError,
    this.buttonKey,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: PaiSpacing.md),
        GlowButton(
          key: buttonKey,
          label: saving ? 'Saving…' : 'Save',
          icon: Icons.check,
          expanded: true,
          loading: saving,
          onPressed: dirty && !saving ? onSave : null,
        ),
        if (message != null)
          Padding(
            padding: const EdgeInsets.only(top: PaiSpacing.sm),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(isError ? Icons.error_outline : Icons.check_circle_outline,
                    size: 16, color: isError ? scheme.error : scheme.primary),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(message!,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(color: isError ? scheme.error : scheme.primary)),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

Widget _panel(Widget child) => Padding(
      padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg),
      child: HudPanel(brackets: false, padding: const EdgeInsets.all(PaiSpacing.md), child: child),
    );

// ---------------------------------------------------------------------------------------------
// This device's name
// ---------------------------------------------------------------------------------------------

class DeviceNameSection extends StatefulWidget {
  final AccountService service;
  final String deviceId;
  final String initialName;
  final ValueChanged<String>? onRenamed;
  const DeviceNameSection({
    super.key,
    required this.service,
    required this.deviceId,
    required this.initialName,
    this.onRenamed,
  });

  @override
  State<DeviceNameSection> createState() => _DeviceNameSectionState();
}

class _DeviceNameSectionState extends State<DeviceNameSection> {
  late final TextEditingController _name = TextEditingController(text: widget.initialName);
  late String _saved = widget.initialName;
  bool _saving = false;
  String? _message;
  bool _isError = false;
  late final ValueNotifier<bool> _dirty = ValueNotifier(false);

  @override
  void initState() {
    super.initState();
    _name.addListener(() => _dirty.value = _name.text.trim() != _saved);
  }

  @override
  void dispose() {
    _name.dispose();
    _dirty.dispose();
    super.dispose();
  }

  String? get _problem {
    final n = _name.text.trim();
    if (n.isEmpty) return 'Give this device a name.';
    if (n.length > AccountLimits.deviceName) return 'At most ${AccountLimits.deviceName} characters.';
    return null;
  }

  Future<void> _save() async {
    final problem = _problem;
    if (problem != null) {
      setState(() {
        _message = problem;
        _isError = true;
      });
      return;
    }
    setState(() {
      _saving = true;
      _message = null;
    });
    try {
      final stored = await widget.service.renameDevice(widget.deviceId, _name.text);
      if (!mounted) return;
      _saved = stored;
      if (_name.text.trim() != stored) _name.text = stored;
      _dirty.value = false;
      widget.onRenamed?.call(stored);
      setState(() {
        _saving = false;
        _message = 'Saved.';
        _isError = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _message = accountErrorMessage(e);
        _isError = true;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return _panel(
      Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('How this device appears in your device list and when PAI asks which device to use.',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: PaiSpacing.sm),
          HudTextField(
            key: const Key('device-name-field'),
            controller: _name,
            label: 'Device name',
            icon: Icons.devices_outlined,
            inputFormatters: [LengthLimitingTextInputFormatter(AccountLimits.deviceName)],
            textInputAction: TextInputAction.done,
            onSubmitted: (_) => _save(),
          ),
          ValueListenableBuilder<bool>(
            valueListenable: _dirty,
            builder: (_, dirty, _) => _SaveRow(
              buttonKey: const Key('device-name-save'),
              dirty: dirty,
              saving: _saving,
              onSave: _save,
              message: _message,
              isError: _isError,
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------------------------
// About you: display name, time zone, language
// ---------------------------------------------------------------------------------------------

const _commonZones = [
  'UTC', 'Africa/Cairo', 'Africa/Johannesburg', 'Africa/Lagos', 'Africa/Nairobi', 'America/Anchorage',
  'America/Argentina/Buenos_Aires', 'America/Bogota', 'America/Chicago', 'America/Denver', 'America/Halifax',
  'America/Los_Angeles', 'America/Mexico_City', 'America/New_York', 'America/Phoenix', 'America/Santiago',
  'America/Sao_Paulo', 'America/Toronto', 'America/Vancouver', 'Asia/Bangkok', 'Asia/Colombo', 'Asia/Dhaka',
  'Asia/Dubai', 'Asia/Hong_Kong', 'Asia/Jakarta', 'Asia/Jerusalem', 'Asia/Karachi', 'Asia/Kathmandu',
  'Asia/Kolkata', 'Asia/Kuala_Lumpur', 'Asia/Manila', 'Asia/Riyadh', 'Asia/Seoul', 'Asia/Shanghai',
  'Asia/Singapore', 'Asia/Taipei', 'Asia/Tehran', 'Asia/Tokyo', 'Atlantic/Reykjavik', 'Australia/Adelaide',
  'Australia/Brisbane', 'Australia/Melbourne', 'Australia/Perth', 'Australia/Sydney', 'Europe/Amsterdam',
  'Europe/Athens', 'Europe/Berlin', 'Europe/Brussels', 'Europe/Dublin', 'Europe/Helsinki', 'Europe/Istanbul',
  'Europe/Lisbon', 'Europe/London', 'Europe/Madrid', 'Europe/Moscow', 'Europe/Oslo', 'Europe/Paris',
  'Europe/Prague', 'Europe/Rome', 'Europe/Stockholm', 'Europe/Vienna', 'Europe/Warsaw', 'Europe/Zurich',
  'Pacific/Auckland', 'Pacific/Honolulu',
];

const _locales = <(String, String)>[
  ('', 'Default'), ('en', 'English'), ('en-IN', 'English (India)'), ('hi', 'हिन्दी'), ('kn', 'ಕನ್ನಡ'),
  ('ta', 'தமிழ்'), ('te', 'తెలుగు'), ('es', 'Español'), ('fr', 'Français'), ('de', 'Deutsch'),
  ('pt', 'Português'), ('ja', '日本語'), ('zh', '中文'),
];

class AboutYouSection extends StatefulWidget {
  final AccountService service;
  const AboutYouSection({super.key, required this.service});

  @override
  State<AboutYouSection> createState() => _AboutYouSectionState();
}

class _AboutYouSectionState extends State<AboutYouSection> {
  final _name = TextEditingController();
  final _tz = TextEditingController();
  final _tzFocus = FocusNode();
  String _locale = '';
  AccountProfile? _saved;
  bool _loading = true;
  bool _saving = false;
  String? _message;
  bool _isError = false;
  final _dirty = ValueNotifier<bool>(false);

  @override
  void initState() {
    super.initState();
    _name.addListener(_recompute);
    _tz.addListener(_recompute);
    _load();
  }

  @override
  void dispose() {
    _name.dispose();
    _tz.dispose();
    _tzFocus.dispose();
    _dirty.dispose();
    super.dispose();
  }

  void _recompute() {
    final s = _saved;
    _dirty.value = s != null &&
        (_name.text.trim() != s.displayName || _tz.text.trim() != s.timezone || _locale != s.locale);
  }

  Future<void> _load() async {
    try {
      final p = await widget.service.getProfile();
      if (!mounted) return;
      _apply(p);
      setState(() => _loading = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _message = accountErrorMessage(e);
        _isError = true;
      });
    }
  }

  void _apply(AccountProfile p) {
    _saved = p;
    _name.text = p.displayName;
    _tz.text = p.timezone;
    _locale = _locales.any((l) => l.$1 == p.locale) ? p.locale : '';
    _recompute();
  }

  Future<void> _save() async {
    final s = _saved;
    if (s == null) return;
    setState(() {
      _saving = true;
      _message = null;
    });
    try {
      final updated = await widget.service.updateProfile(
        displayName: _name.text.trim() != s.displayName ? _name.text : null,
        timezone: _tz.text.trim() != s.timezone ? _tz.text : null,
        locale: _locale != s.locale ? _locale : null,
      );
      if (!mounted) return;
      _apply(updated);
      setState(() {
        _saving = false;
        _message = 'Saved.';
        _isError = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _message = accountErrorMessage(e);
        _isError = true;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Padding(padding: EdgeInsets.all(PaiSpacing.lg), child: LinearProgressIndicator());
    if (_saved == null) {
      return _panel(Row(children: [
        Expanded(child: Text(_message ?? 'Could not load your profile.', style: TextStyle(color: Theme.of(context).colorScheme.error))),
        TextButton(onPressed: () => setState(() { _loading = true; _message = null; _load(); }), child: const Text('Retry')),
      ]));
    }
    final now = DateTime.now();
    final offset = now.timeZoneOffset;
    final sign = offset.isNegative ? '-' : '+';
    final hh = offset.abs().inHours.toString().padLeft(2, '0');
    final mm = (offset.abs().inMinutes % 60).toString().padLeft(2, '0');
    return _panel(
      Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          HudTextField(
            key: const Key('display-name-field'),
            controller: _name,
            label: 'Your name',
            hint: _saved!.email,
            icon: Icons.person_outline,
            inputFormatters: [LengthLimitingTextInputFormatter(AccountLimits.displayName)],
          ),
          const SizedBox(height: PaiSpacing.md),
          _TimeZoneField(controller: _tz, focusNode: _tzFocus),
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text('This device: ${now.timeZoneName} (UTC$sign$hh:$mm). Used for reminders and schedules.',
                style: Theme.of(context).textTheme.bodySmall),
          ),
          const SizedBox(height: PaiSpacing.md),
          DropdownButtonFormField<String>(
            key: const Key('locale-field'),
            initialValue: _locale,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Language', isDense: true),
            items: [for (final l in _locales) DropdownMenuItem(value: l.$1, child: Text(l.$2))],
            onChanged: (v) {
              _locale = v ?? '';
              _recompute();
            },
          ),
          ValueListenableBuilder<bool>(
            valueListenable: _dirty,
            builder: (_, dirty, _) => _SaveRow(
              buttonKey: const Key('about-you-save'),
              dirty: dirty,
              saving: _saving,
              onSave: _save,
              message: _message,
              isError: _isError,
            ),
          ),
        ],
      ),
    );
  }
}

/// IANA time zone: type to search the common ones, or type any valid name (the server checks it).
class _TimeZoneField extends StatelessWidget {
  final TextEditingController controller;
  final FocusNode focusNode;
  const _TimeZoneField({required this.controller, required this.focusNode});

  @override
  Widget build(BuildContext context) {
    return RawAutocomplete<String>(
      textEditingController: controller,
      focusNode: focusNode,
      optionsBuilder: (v) {
        final q = v.text.trim().toLowerCase().replaceAll(' ', '_');
        if (q.isEmpty) return const Iterable<String>.empty();
        return _commonZones.where((z) => z.toLowerCase().contains(q)).take(8);
      },
      fieldViewBuilder: (context, c, f, onSubmit) => HudTextField(
        key: const Key('timezone-field'),
        controller: c,
        focusNode: f,
        label: 'Time zone',
        hint: 'e.g. Asia/Kolkata',
        icon: Icons.schedule,
        onSubmitted: (_) => onSubmit(),
      ),
      optionsViewBuilder: (context, onSelected, options) => Align(
        alignment: Alignment.topLeft,
        child: Material(
          elevation: 4,
          borderRadius: BorderRadius.circular(8),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 220, maxWidth: 420),
            child: ListView(
              padding: EdgeInsets.zero,
              shrinkWrap: true,
              children: [
                for (final o in options)
                  ListTile(dense: true, title: Text(o), onTap: () => onSelected(o)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------------------------
// Personalization
// ---------------------------------------------------------------------------------------------

class PersonalizationSection extends StatefulWidget {
  final AccountService service;
  const PersonalizationSection({super.key, required this.service});

  @override
  State<PersonalizationSection> createState() => _PersonalizationSectionState();
}

class _PersonalizationSectionState extends State<PersonalizationSection> {
  final _preferred = TextEditingController();
  final _occupation = TextEditingController();
  final _about = TextEditingController();

  Personalization _saved = const Personalization();
  // Chips and choices live in one value; the text fields are read from their controllers when needed.
  Personalization _draft = const Personalization();
  bool _loading = true;
  bool _saving = false;
  bool _loadFailed = false;
  String? _message;
  bool _isError = false;
  final _dirty = ValueNotifier<bool>(false);

  @override
  void initState() {
    super.initState();
    for (final c in [_preferred, _occupation, _about]) {
      c.addListener(_recompute);
    }
    _load();
  }

  @override
  void dispose() {
    _preferred.dispose();
    _occupation.dispose();
    _about.dispose();
    _dirty.dispose();
    super.dispose();
  }

  Personalization get _current => _draft.copyWith(
        preferredName: _preferred.text.trim(),
        occupation: _occupation.text.trim(),
        about: _about.text.trim(),
      );

  void _recompute() => _dirty.value = !_loading && _current != _saved;

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadFailed = false;
    });
    try {
      final p = await widget.service.getPersonalization();
      if (!mounted) return;
      _apply(p);
      setState(() => _loading = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _loadFailed = true;
        _message = accountErrorMessage(e);
        _isError = true;
      });
    }
  }

  void _apply(Personalization p) {
    _saved = p;
    _draft = p;
    _preferred.text = p.preferredName;
    _occupation.text = p.occupation;
    _about.text = p.about;
    _loading = false;
    _recompute();
  }

  /// Apply a change to the LATEST draft (two quick taps must not overwrite each other).
  void _update(Personalization Function(Personalization d) change) {
    setState(() => _draft = change(_draft));
    _recompute();
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _message = null;
    });
    try {
      final stored = await widget.service.savePersonalization(_current);
      if (!mounted) return;
      _apply(stored);
      setState(() {
        _saving = false;
        _message = 'Saved. PAI will use this to personalise its replies.';
        _isError = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _message = accountErrorMessage(e);
        _isError = true;
      });
    }
  }

  void _clearAll() {
    _preferred.clear();
    _occupation.clear();
    _about.clear();
    _update((_) => const Personalization());
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Padding(padding: EdgeInsets.all(PaiSpacing.lg), child: LinearProgressIndicator());
    if (_loadFailed) {
      return _panel(Row(children: [
        Expanded(child: Text(_message ?? 'Could not load.', style: TextStyle(color: Theme.of(context).colorScheme.error))),
        TextButton(onPressed: _load, child: const Text('Retry')),
      ]));
    }
    final text = Theme.of(context).textTheme;
    final d = _draft;
    return _panel(
      Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Only used to personalise PAI\'s replies to you. Nothing here is shared, and you can clear it any time.',
            style: text.bodySmall,
          ),
          const SizedBox(height: PaiSpacing.md),
          HudTextField(
            key: const Key('preferred-name-field'),
            controller: _preferred,
            label: 'What should PAI call you?',
            icon: Icons.badge_outlined,
            inputFormatters: [LengthLimitingTextInputFormatter(AccountLimits.preferredName)],
          ),
          const SizedBox(height: PaiSpacing.md),
          HudTextField(
            key: const Key('occupation-field'),
            controller: _occupation,
            label: 'What do you do?',
            icon: Icons.work_outline,
            inputFormatters: [LengthLimitingTextInputFormatter(AccountLimits.occupation)],
          ),
          const SizedBox(height: PaiSpacing.md),
          HudTextField(
            key: const Key('about-field'),
            controller: _about,
            label: 'Anything else PAI should know about you',
            icon: Icons.notes,
            maxLines: 4,
            inputFormatters: [LengthLimitingTextInputFormatter(AccountLimits.about)],
          ),
          const SizedBox(height: PaiSpacing.lg),
          ChipInput(label: 'Likes', values: d.likes, onChanged: (v) => _update((d) => d.copyWith(likes: v)), hint: 'e.g. jazz, spicy food'),
          const SizedBox(height: PaiSpacing.md),
          ChipInput(label: 'Dislikes', values: d.dislikes, onChanged: (v) => _update((d) => d.copyWith(dislikes: v)), hint: 'e.g. small talk, loud music'),
          const SizedBox(height: PaiSpacing.md),
          ChipInput(label: 'Interests', values: d.interests, onChanged: (v) => _update((d) => d.copyWith(interests: v)), hint: 'e.g. space, machine learning'),
          const SizedBox(height: PaiSpacing.md),
          ChipInput(label: 'Hobbies', values: d.hobbies, onChanged: (v) => _update((d) => d.copyWith(hobbies: v)), hint: 'e.g. chess, hiking'),
          const SizedBox(height: PaiSpacing.lg),
          _Choice(
            label: 'Tone',
            options: personalizationTones,
            value: d.tone,
            keyPrefix: 'tone',
            onChanged: (v) => _update((d) => d.copyWith(tone: v)),
          ),
          const SizedBox(height: PaiSpacing.md),
          _Choice(
            label: 'Detail',
            options: personalizationDetails,
            value: d.detail,
            keyPrefix: 'detail',
            onChanged: (v) => _update((d) => d.copyWith(detail: v)),
          ),
          const SizedBox(height: PaiSpacing.sm),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              key: const Key('personalization-clear'),
              onPressed: _clearAll,
              icon: const Icon(Icons.delete_outline, size: 18),
              label: const Text('Clear everything'),
            ),
          ),
          ValueListenableBuilder<bool>(
            valueListenable: _dirty,
            builder: (_, dirty, _) => _SaveRow(
              buttonKey: const Key('personalization-save'),
              dirty: dirty,
              saving: _saving,
              onSave: _save,
              message: _message,
              isError: _isError,
            ),
          ),
        ],
      ),
    );
  }
}

/// One of a few choices. Chips wrap, so four options still fit a 320 px wide phone.
class _Choice extends StatelessWidget {
  final String label;
  final List<String> options;
  final String value;
  final String keyPrefix;
  final ValueChanged<String> onChanged;
  const _Choice({required this.label, required this.options, required this.value, required this.keyPrefix, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Theme.of(context).textTheme.labelLarge),
        const SizedBox(height: PaiSpacing.xs),
        Wrap(
          spacing: 8,
          runSpacing: 4,
          children: [
            for (final o in options)
              ChoiceChip(
                key: Key('$keyPrefix-$o'),
                label: Text(o[0].toUpperCase() + o.substring(1)),
                selected: value == o,
                onSelected: (_) => onChanged(o),
              ),
          ],
        ),
      ],
    );
  }
}
