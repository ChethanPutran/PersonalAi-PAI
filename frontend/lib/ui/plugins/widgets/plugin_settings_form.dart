import 'dart:convert';

import 'package:flutter/material.dart';

import '../../../plugins/plugin_settings.dart';
import '../../theme/theme.dart';

/// True when two setting values are the same (lists compare by content).
bool sameSettingValue(Object? a, Object? b) => jsonEncode(a) == jsonEncode(b);

/// What to send to the server: the keys of [current] that differ from [base]. A key that goes back to its
/// default is sent as `null` (the server's "reset"), so only real choices stay stored.
Map<String, dynamic> settingsChanges(PluginSettings s, Map<String, dynamic> base, Map<String, dynamic> current) {
  final out = <String, dynamic>{};
  for (final f in s.fields) {
    if (!current.containsKey(f.key)) continue;
    if (sameSettingValue(base[f.key], current[f.key])) continue;
    out[f.key] = sameSettingValue(s.defaults[f.key], current[f.key]) ? null : current[f.key];
  }
  return out;
}

/// Why [raw] cannot be added to a list of folders, or null. Mirrors the server's rule (it checks again).
String? folderProblem(String raw) {
  final p = raw.trim();
  if (p.isEmpty) return 'Type a folder path first.';
  if (p.contains('\u0000')) return 'That is not a valid path.';
  if (!(p.startsWith('/') || p == '~' || p.startsWith('~/'))) return 'Use a full path that starts with /, for example /storage/emulated/0/Download.';
  if (p.split('/').contains('..')) return 'A path cannot contain "..".';
  return null;
}

/// A form built from a plugin's `configSchema`: switches for booleans, a folder-list editor for `paths`
/// lists (remove buttons, add field, one-tap suggestion chips), text / number / choice fields otherwise.
/// Each field shows its default and any error the server gave for it ([errors], keyed by setting).
///
/// The form owns the values it edits; [onChanged] is called with the full current map after every edit.
class PluginSettingsForm extends StatefulWidget {
  final PluginSettings settings;

  /// The values to start from (usually `settings.effective`).
  final Map<String, dynamic> initial;
  final Map<String, String> errors;
  final bool enabled;
  final ValueChanged<Map<String, dynamic>> onChanged;

  const PluginSettingsForm({
    super.key,
    required this.settings,
    required this.initial,
    required this.onChanged,
    this.errors = const {},
    this.enabled = true,
  });

  @override
  State<PluginSettingsForm> createState() => _PluginSettingsFormState();
}

class _PluginSettingsFormState extends State<PluginSettingsForm> {
  late Map<String, dynamic> _values;
  final Map<String, TextEditingController> _text = {};
  final Map<String, String> _localErrors = {};

  @override
  void initState() {
    super.initState();
    _values = _copy(widget.initial);
    for (final f in widget.settings.fields) {
      _values.putIfAbsent(f.key, () => _copyValue(f.defaultValue));
    }
  }

  @override
  void dispose() {
    for (final c in _text.values) {
      c.dispose();
    }
    super.dispose();
  }

  static Map<String, dynamic> _copy(Map<String, dynamic> m) => {for (final e in m.entries) e.key: _copyValue(e.value)};
  static Object? _copyValue(Object? v) => v is List ? List<Object?>.from(v) : v;

  TextEditingController _ctrl(String key, [String initial = '']) => _text.putIfAbsent(key, () => TextEditingController(text: initial));

  void _set(String key, Object? value) {
    setState(() {
      _values[key] = value;
      _localErrors.remove(key);
    });
    widget.onChanged(_copy(_values));
  }

  List<String> _list(String key) => ((_values[key] as List?) ?? const []).map((e) => e.toString()).toList();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final f in widget.settings.fields) ...[
          _field(context, f),
          const SizedBox(height: PaiSpacing.lg),
        ],
      ],
    );
  }

  Widget _field(BuildContext context, SettingField f) {
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final error = widget.errors[f.key] ?? _localErrors[f.key];
    final isDefault = sameSettingValue(_values[f.key], f.defaultValue);
    final body = switch (f.kind) {
      SettingKind.boolean => _boolField(f),
      SettingKind.stringList => f.isPaths ? _pathsField(context, f) : _listField(context, f),
      SettingKind.string when f.options.isNotEmpty => _choiceField(f),
      SettingKind.string || SettingKind.integer || SettingKind.number => _textField(f),
      SettingKind.unsupported => Text('This setting cannot be edited in the app.', style: text.bodySmall),
    };
    return Column(
      key: Key('setting-${f.key}'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (f.kind != SettingKind.boolean) Text(f.title, style: text.titleSmall),
        if (f.description.isNotEmpty && f.kind != SettingKind.boolean)
          Padding(padding: const EdgeInsets.only(top: 2), child: Text(f.description, style: text.bodySmall)),
        const SizedBox(height: PaiSpacing.xs),
        body,
        if (error != null)
          Padding(
            padding: const EdgeInsets.only(top: PaiSpacing.xs),
            child: Row(
              key: Key('setting-error-${f.key}'),
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.error_outline, size: 16, color: scheme.error),
                const SizedBox(width: 6),
                Expanded(child: Text(error, style: text.bodySmall?.copyWith(color: scheme.error))),
              ],
            ),
          ),
        if (f.defaultValue != null)
          Padding(
            padding: const EdgeInsets.only(top: PaiSpacing.xs),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    'Default: ${_describeDefault(f)}',
                    key: Key('setting-default-${f.key}'),
                    style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                ),
                if (!isDefault && widget.enabled)
                  TextButton(
                    key: Key('setting-reset-${f.key}'),
                    onPressed: () {
                      final d = _copyValue(f.defaultValue);
                      _text.remove(f.key)?.dispose();
                      _set(f.key, d);
                    },
                    child: const Text('Reset'),
                  ),
              ],
            ),
          ),
      ],
    );
  }

  String _describeDefault(SettingField f) {
    final d = f.defaultValue;
    if (d is bool) return d ? 'on' : 'off';
    if (d is List) return d.isEmpty ? 'none' : d.join(', ');
    return d.toString();
  }

  // ---- booleans ----------------------------------------------------------------------------

  Widget _boolField(SettingField f) => SwitchListTile(
        key: Key('setting-switch-${f.key}'),
        contentPadding: EdgeInsets.zero,
        title: Text(f.title),
        subtitle: f.description.isEmpty ? null : Text(f.description),
        value: _values[f.key] == true,
        onChanged: widget.enabled ? (v) => _set(f.key, v) : null,
      );

  // ---- folder lists ------------------------------------------------------------------------

  Widget _pathsField(BuildContext context, SettingField f) {
    final items = _list(f.key);
    final atMax = f.maxItems != null && items.length >= f.maxItems!;
    final add = _ctrl('add:${f.key}');
    void submit() {
      final raw = add.text.trim();
      final problem = folderProblem(raw);
      if (problem != null) {
        setState(() => _localErrors[f.key] = problem);
        return;
      }
      var p = raw;
      while (p.length > 1 && p.endsWith('/')) {
        p = p.substring(0, p.length - 1);
      }
      if (items.contains(p)) {
        setState(() => _localErrors[f.key] = 'That folder is already in the list.');
        return;
      }
      add.clear();
      _set(f.key, [...items, p]);
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (items.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: PaiSpacing.xs),
            child: Text('No folders.', key: Key('paths-empty-${f.key}'), style: Theme.of(context).textTheme.bodySmall),
          ),
        for (var i = 0; i < items.length; i++)
          Row(
            key: Key('paths-row-${f.key}-$i'),
            children: [
              const Icon(Icons.folder_outlined, size: 18),
              const SizedBox(width: PaiSpacing.sm),
              Expanded(child: Text(items[i], style: context.pai.mono(size: 12))),
              IconButton(
                key: Key('paths-remove-${f.key}-$i'),
                tooltip: 'Remove ${items[i]}',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.close, size: 18),
                onPressed: widget.enabled ? () => _set(f.key, [...items]..removeAt(i)) : null,
              ),
            ],
          ),
        const SizedBox(height: PaiSpacing.xs),
        Row(
          children: [
            Expanded(
              child: TextField(
                key: Key('paths-add-${f.key}'),
                controller: add,
                enabled: widget.enabled && !atMax,
                autocorrect: false,
                enableSuggestions: false,
                keyboardType: TextInputType.url,
                textInputAction: TextInputAction.done,
                decoration: InputDecoration(
                  isDense: true,
                  hintText: atMax ? 'Limit of ${f.maxItems} folders reached' : '/storage/emulated/0/Download',
                  prefixIcon: const Icon(Icons.create_new_folder_outlined, size: 18),
                ),
                onSubmitted: (_) => submit(),
              ),
            ),
            const SizedBox(width: PaiSpacing.sm),
            FilledButton.tonal(
              key: Key('paths-add-btn-${f.key}'),
              onPressed: widget.enabled && !atMax ? submit : null,
              child: const Text('Add'),
            ),
          ],
        ),
        if (f.suggestions.isNotEmpty) ...[
          const SizedBox(height: PaiSpacing.sm),
          Wrap(
            spacing: PaiSpacing.sm,
            runSpacing: PaiSpacing.xs,
            children: [
              for (final s in f.suggestions)
                ActionChip(
                  key: Key('paths-suggest-${f.key}-${s.path}'),
                  avatar: Icon(items.contains(s.path) ? Icons.check : Icons.add, size: 16),
                  label: Text(s.label),
                  tooltip: s.path,
                  onPressed: widget.enabled && !items.contains(s.path) && !atMax ? () => _set(f.key, [...items, s.path]) : null,
                ),
            ],
          ),
        ],
      ],
    );
  }

  /// A plain list of text values (not folders): same editor without the folder rules.
  Widget _listField(BuildContext context, SettingField f) {
    final items = _list(f.key);
    final add = _ctrl('add:${f.key}');
    void submit() {
      final v = add.text.trim();
      if (v.isEmpty || items.contains(v)) return;
      add.clear();
      _set(f.key, [...items, v]);
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < items.length; i++)
          Row(children: [
            Expanded(child: Text(items[i])),
            IconButton(
              key: Key('list-remove-${f.key}-$i'),
              icon: const Icon(Icons.close, size: 18),
              onPressed: widget.enabled ? () => _set(f.key, [...items]..removeAt(i)) : null,
            ),
          ]),
        Row(children: [
          Expanded(child: TextField(key: Key('list-add-${f.key}'), controller: add, enabled: widget.enabled, onSubmitted: (_) => submit())),
          TextButton(onPressed: widget.enabled ? submit : null, child: const Text('Add')),
        ]),
      ],
    );
  }

  // ---- text, numbers, choices --------------------------------------------------------------

  Widget _textField(SettingField f) {
    final isNum = f.kind == SettingKind.integer || f.kind == SettingKind.number;
    final c = _ctrl(f.key, _values[f.key]?.toString() ?? '');
    return TextField(
      key: Key('setting-input-${f.key}'),
      controller: c,
      enabled: widget.enabled,
      keyboardType: isNum ? const TextInputType.numberWithOptions(decimal: true, signed: true) : TextInputType.text,
      decoration: const InputDecoration(isDense: true),
      onChanged: (t) {
        if (!isNum) return _set(f.key, t);
        final n = f.kind == SettingKind.integer ? int.tryParse(t.trim()) : num.tryParse(t.trim());
        if (n == null) {
          setState(() => _localErrors[f.key] = 'Enter a number.');
          return;
        }
        _set(f.key, n);
      },
    );
  }

  Widget _choiceField(SettingField f) => DropdownButtonFormField<String>(
        key: Key('setting-choice-${f.key}'),
        initialValue: f.options.contains(_values[f.key]) ? _values[f.key] as String : null,
        isDense: true,
        items: [for (final o in f.options) DropdownMenuItem(value: o, child: Text(o))],
        onChanged: widget.enabled ? (v) => v == null ? null : _set(f.key, v) : null,
      );
}
