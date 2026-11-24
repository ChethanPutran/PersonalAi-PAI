import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../schedules/param_form.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';

/// State behind a [ParamForm]: one text controller (or selected value) per schema field, plus
/// the validation errors to show — local ones from [validate] and the server's from
/// [setServerErrors] (the server is authoritative).
class ParamFormController extends ChangeNotifier {
  ParamFormController(this.spec, {Map<String, dynamic>? existing}) {
    final initial = spec.initialRaw(existing);
    for (final f in spec.fields) {
      final v = initial[f.name] ?? '';
      if (f is EnumParam || f is BoolParam) {
        _selected[f.name] = v;
      } else {
        final c = TextEditingController(text: v);
        var last = v;
        // Selection/focus changes also notify; only a real text change is an edit.
        c.addListener(() {
          if (c.text != last) {
            last = c.text;
            onEdited?.call();
          }
        });
        _text[f.name] = c;
      }
    }
  }

  final ParamFormSpec spec;
  final Map<String, TextEditingController> _text = {};
  final Map<String, String> _selected = {};
  Map<String, String> _errors = const {};

  /// Called only when the USER changes something (not on validate / server errors).
  VoidCallback? onEdited;

  Map<String, String> get errors => _errors;
  TextEditingController? textFor(String name) => _text[name];
  String selectedFor(String name) => _selected[name] ?? '';

  void select(String name, String value) {
    _selected[name] = value;
    _clearError(name);
    onEdited?.call();
    notifyListeners();
  }

  void _clearError(String name) {
    if (_errors.containsKey(name)) {
      _errors = Map.of(_errors)..remove(name);
    }
  }

  Map<String, String> raw() => {
        for (final f in spec.fields)
          f.name: (f is EnumParam || f is BoolParam) ? selectedFor(f.name) : (_text[f.name]?.text ?? ''),
      };

  /// Validates every field locally, showing the errors; returns the result.
  ParamCollectResult validate() {
    final result = spec.collect(raw());
    _errors = result.errors;
    notifyListeners();
    return result;
  }

  /// The current typed values without showing errors (null if currently invalid).
  Map<String, Object?>? valuesOrNull() {
    final r = spec.collect(raw());
    return r.isValid ? r.values : null;
  }

  /// Show errors reported by the server next to the matching inputs.
  void setServerErrors(Map<String, String> errors) {
    _errors = Map.of(errors);
    notifyListeners();
  }

  @override
  void dispose() {
    for (final c in _text.values) {
      c.dispose();
    }
    super.dispose();
  }
}

/// The generated parameter inputs for one capability's JSON Schema.
class ParamForm extends StatelessWidget {
  const ParamForm({super.key, required this.controller});
  final ParamFormController controller;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final spec = controller.spec;
        if (spec.isEmpty) {
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: PaiSpacing.sm),
            child: Text('This action takes no parameters.',
                style: Theme.of(context).textTheme.bodySmall?.copyWith(color: context.pai.muted)),
          );
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (final f in spec.fields) ...[
              _field(context, f),
              const SizedBox(height: PaiSpacing.md),
            ],
          ],
        );
      },
    );
  }

  String _label(ParamField f) => f.required ? '${f.label} *' : f.label;

  Widget _field(BuildContext context, ParamField f) {
    final err = controller.errors[f.name];
    final muted = Theme.of(context).textTheme.bodySmall?.copyWith(color: context.pai.muted);
    final helper = [if (f.description != null) f.description!, if (f.hint != null) f.hint!].join('\n');

    Widget input;
    if (f is BoolParam) {
      input = SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: Text(_label(f)),
        subtitle: f.description == null ? null : Text(f.description!, style: muted),
        value: controller.selectedFor(f.name) == 'true',
        onChanged: (v) => controller.select(f.name, v.toString()),
      );
      return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        input,
        if (err != null) _error(context, err),
      ]);
    }
    if (f is EnumParam) {
      final current = controller.selectedFor(f.name);
      final options = f.options.map((o) => o.toString()).toList();
      input = DropdownButtonFormField<String>(
        initialValue: options.contains(current) ? current : null,
        isExpanded: true,
        decoration: InputDecoration(labelText: _label(f), errorText: err),
        items: [for (final o in options) DropdownMenuItem(value: o, child: Text(o))],
        onChanged: (v) {
          if (v != null) controller.select(f.name, v);
        },
      );
    } else {
      final numeric = f is NumberParam;
      final isJson = f is JsonParam;
      input = HudTextField(
        controller: controller.textFor(f.name),
        label: _label(f),
        errorText: err,
        maxLines: isJson ? 4 : 1,
        keyboardType: numeric
            ? TextInputType.numberWithOptions(decimal: !(f).integer, signed: true)
            : (isJson ? TextInputType.multiline : TextInputType.text),
        inputFormatters: numeric
            ? [FilteringTextInputFormatter.allow(RegExp(r'[0-9.\-eE+]'))]
            : null,
      );
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      input,
      if (helper.isNotEmpty && err == null)
        Padding(padding: const EdgeInsets.only(top: 4, left: 4), child: Text(helper, style: muted)),
    ]);
  }

  Widget _error(BuildContext context, String message) => Padding(
        padding: const EdgeInsets.only(top: 2, left: 4),
        child: Text(message, style: TextStyle(color: Theme.of(context).colorScheme.error, fontSize: 12)),
      );
}
