import 'package:flutter/material.dart';

import 'json_editors.dart';


/// Renders the correct input widget for a capability parameter, based
/// on the parameter's JSON Schema `type`.
///
///   string / number / integer → TextFormField
///   boolean                   → SwitchListTile
///   enum                      → DropdownButtonFormField
///   object                    → KeyValueEditor
///   array                     → JsonArrayEditor
///
/// The parent owns the current value and rebuilds via [onChanged].
class ParamField extends StatefulWidget {
  final String name;
  final Map<String, dynamic> schema;
  final dynamic value;
  final ValueChanged<dynamic> onChanged;

  const ParamField({
    super.key,
    required this.name,
    required this.schema,
    required this.value,
    required this.onChanged,
  });

  @override
  State<ParamField> createState() => _ParamFieldState();
}

class _ParamFieldState extends State<ParamField> {
  TextEditingController? _textCtrl;
  dynamic _lastEmitted;

  @override
  void initState() {
    super.initState();
    _lastEmitted = widget.value;
    _setupController();
  }

  @override
  void didUpdateWidget(ParamField oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Only resync the text controller when the value came from outside
    // (e.g. a "Reset" button). Our own emissions are ignored so the
    // user's typed formatting ("1.50" vs "1.5") is preserved.
    if (!identical(_lastEmitted, widget.value) &&
        _lastEmitted != widget.value &&
        _textCtrl != null) {
      final incoming = widget.value?.toString() ?? '';
      if (_textCtrl!.text != incoming) {
        _textCtrl!.text = incoming;
      }
      _lastEmitted = widget.value;
    }
  }

  @override
  void dispose() {
    _textCtrl?.dispose();
    super.dispose();
  }

  String get _type => widget.schema['type'] as String? ?? 'string';
  List? get _enums => widget.schema['enum'] as List?;

  bool get _isTextField =>
      _enums == null || _enums!.isEmpty
          ? _type != 'boolean' && _type != 'object' && _type != 'array'
          : false;

  void _setupController() {
    if (_isTextField) {
      _textCtrl =
          TextEditingController(text: widget.value?.toString() ?? '');
    }
  }

  void _emit(dynamic v) {
    _lastEmitted = v;
    widget.onChanged(v);
  }

  @override
  Widget build(BuildContext context) {
    final desc = widget.schema['description'] as String? ?? '';

    Widget input;

    if (_type == 'object') {
      final v = widget.value;
      input = KeyValueEditor(
        value: v is Map ? Map<String, dynamic>.from(v) : const {},
        onChanged: (m) => _emit(m),
      );
    } else if (_type == 'array') {
      final v = widget.value;
      input = JsonArrayEditor(
        value: v is List ? List<dynamic>.from(v) : const [],
        onChanged: (l) => _emit(l),
      );
    } else if (_enums != null && _enums!.isNotEmpty) {
      // Guard: value must be in the enum or DropdownButtonFormField
      // asserts at build time.
      final current = _enums!.contains(widget.value)
          ? widget.value
          : _enums!.first;
      input = DropdownButtonFormField<dynamic>(
        value: current,
        items: _enums!
            .map((v) =>
                DropdownMenuItem(value: v, child: Text(v.toString())))
            .toList(),
        onChanged: (v) => _emit(v),
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    } else if (_type == 'boolean') {
      final v = widget.value == true;
      input = SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: Text(v ? 'true' : 'false',
            style: const TextStyle(fontSize: 13)),
        value: v,
        onChanged: (b) => _emit(b),
      );
    } else {
      final isNum = _type == 'integer' || _type == 'number';
      input = TextFormField(
        controller: _textCtrl,
        keyboardType: isNum
            ? TextInputType.numberWithOptions(decimal: _type == 'number')
            : TextInputType.text,
        onChanged: (s) {
          if (_type == 'integer') {
            _emit(int.tryParse(s));
          } else if (_type == 'number') {
            _emit(double.tryParse(s));
          } else {
            _emit(s);
          }
        },
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.name,
            style: const TextStyle(fontWeight: FontWeight.w500),
          ),
          if (desc.isNotEmpty)
            Text(
              desc,
              style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
            ),
          const SizedBox(height: 4),
          input,
        ],
      ),
    );
  }
}