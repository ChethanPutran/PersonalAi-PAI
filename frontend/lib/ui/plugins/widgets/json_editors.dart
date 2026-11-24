import 'dart:convert';

import 'package:flutter/material.dart';


/// Editor for an object-typed parameter (e.g. `browser.fill_form.fields`).
///
/// Shows each key/value as an editable row, with add/remove controls.
/// Emits the whole map on every change.
///
/// Behaviour:
///   • Starts with one empty row if the incoming value is empty, so the
///     user sees the shape of the input immediately.
///   • Empty keys are skipped on emit — a half-typed key doesn't
///     produce an invalid map.
///   • If the parent changes [value] externally, [didUpdateWidget]
///     re-syncs the rows only when the diff is real; editing a field
///     doesn't get clobbered.
class KeyValueEditor extends StatefulWidget {
  final Map<String, dynamic> value;
  final ValueChanged<Map<String, dynamic>> onChanged;
  final String keyHint;
  final String valueHint;

  const KeyValueEditor({
    super.key,
    required this.value,
    required this.onChanged,
    this.keyHint = 'key',
    this.valueHint = 'value',
  });

  @override
  State<KeyValueEditor> createState() => _KeyValueEditorState();
}

class _KeyValueEditorState extends State<KeyValueEditor> {
  final List<_KVRow> _rows = [];
  int _nextId = 0;
  Map<String, dynamic> _lastEmitted = const {};

  @override
  void initState() {
    super.initState();
    _lastEmitted = Map<String, dynamic>.from(widget.value);
    _syncFromValue(widget.value);
  }

  @override
  void didUpdateWidget(KeyValueEditor oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!_mapsEqual(_lastEmitted, widget.value)) {
      _lastEmitted = Map<String, dynamic>.from(widget.value);
      _disposeRows();
      _syncFromValue(widget.value);
    }
  }

  @override
  void dispose() {
    _disposeRows();
    super.dispose();
  }

  void _disposeRows() {
    for (final r in _rows) {
      r.keyCtrl.dispose();
      r.valueCtrl.dispose();
    }
    _rows.clear();
  }

  void _syncFromValue(Map<String, dynamic> v) {
    _rows.clear();
    v.forEach((k, val) {
      _rows.add(_KVRow(
        id: _nextId++,
        keyCtrl: TextEditingController(text: k),
        valueCtrl: TextEditingController(text: val?.toString() ?? ''),
      ));
    });
    if (_rows.isEmpty) {
      _rows.add(_KVRow(
        id: _nextId++,
        keyCtrl: TextEditingController(),
        valueCtrl: TextEditingController(),
      ));
    }
  }

  Map<String, dynamic> _currentMap() {
    final out = <String, dynamic>{};
    for (final r in _rows) {
      final k = r.keyCtrl.text.trim();
      if (k.isEmpty) continue;
      out[k] = r.valueCtrl.text;
    }
    return out;
  }

  bool _mapsEqual(Map a, Map b) {
    if (a.length != b.length) return false;
    for (final k in a.keys) {
      if (!b.containsKey(k)) return false;
      if (a[k]?.toString() != b[k]?.toString()) return false;
    }
    return true;
  }

  void _addRow() {
    setState(() {
      _rows.add(_KVRow(
        id: _nextId++,
        keyCtrl: TextEditingController(),
        valueCtrl: TextEditingController(),
      ));
    });
  }

  void _removeRow(_KVRow row) {
    setState(() {
      row.keyCtrl.dispose();
      row.valueCtrl.dispose();
      _rows.remove(row);
    });
    _emit();
  }

  void _emit() {
    final m = _currentMap();
    _lastEmitted = m;
    widget.onChanged(m);
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ..._rows.map((r) => Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                children: [
                  Expanded(
                    flex: 2,
                    child: TextFormField(
                      controller: r.keyCtrl,
                      decoration: InputDecoration(
                        hintText: widget.keyHint,
                        border: const OutlineInputBorder(),
                        isDense: true,
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 10,
                        ),
                      ),
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 12,
                      ),
                      onChanged: (_) => _emit(),
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    flex: 3,
                    child: TextFormField(
                      controller: r.valueCtrl,
                      decoration: InputDecoration(
                        hintText: widget.valueHint,
                        border: const OutlineInputBorder(),
                        isDense: true,
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 10,
                        ),
                      ),
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 12,
                      ),
                      onChanged: (_) => _emit(),
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close, size: 18),
                    tooltip: 'Remove',
                    onPressed: () => _removeRow(r),
                  ),
                ],
              ),
            )),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            icon: const Icon(Icons.add, size: 16),
            label: const Text('Add field'),
            onPressed: _addRow,
          ),
        ),
      ],
    );
  }
}

class _KVRow {
  final int id;
  final TextEditingController keyCtrl;
  final TextEditingController valueCtrl;
  _KVRow({
    required this.id,
    required this.keyCtrl,
    required this.valueCtrl,
  });
}


/// Editor for an array-typed parameter (e.g. `browser.book.steps`).
///
/// Each item is edited as raw JSON (usually an object like
/// `{"action": "fill", "selector": "...", "value": "..."}`). Invalid
/// JSON items are skipped on emit so a half-typed item doesn't produce
/// a broken list; the parent sees the last valid list.
class JsonArrayEditor extends StatefulWidget {
  final List<dynamic> value;
  final ValueChanged<List<dynamic>> onChanged;
  final String itemHint;

  const JsonArrayEditor({
    super.key,
    required this.value,
    required this.onChanged,
    this.itemHint = '{"action": "click", "selector": "#button"}',
  });

  @override
  State<JsonArrayEditor> createState() => _JsonArrayEditorState();
}

class _JsonArrayEditorState extends State<JsonArrayEditor> {
  final List<_ArrayItem> _items = [];
  int _nextId = 0;
  List<dynamic> _lastEmitted = const [];

  @override
  void initState() {
    super.initState();
    _lastEmitted = List<dynamic>.from(widget.value);
    _syncFromValue(widget.value);
  }

  @override
  void didUpdateWidget(JsonArrayEditor oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!_listsEqual(_lastEmitted, widget.value)) {
      _lastEmitted = List<dynamic>.from(widget.value);
      _disposeItems();
      _syncFromValue(widget.value);
    }
  }

  @override
  void dispose() {
    _disposeItems();
    super.dispose();
  }

  void _disposeItems() {
    for (final it in _items) {
      it.ctrl.dispose();
    }
    _items.clear();
  }

  void _syncFromValue(List<dynamic> v) {
    _items.clear();
    for (final entry in v) {
      _items.add(_ArrayItem(
        id: _nextId++,
        ctrl: TextEditingController(
          text: const JsonEncoder.withIndent('  ').convert(entry),
        ),
      ));
    }
    if (_items.isEmpty) {
      _items.add(_ArrayItem(id: _nextId++, ctrl: TextEditingController()));
    }
  }

  List<dynamic> _currentList() {
    final out = <dynamic>[];
    for (final it in _items) {
      final text = it.ctrl.text.trim();
      if (text.isEmpty) continue;
      try {
        out.add(jsonDecode(text));
      } catch (_) {
        // Skip invalid — user is mid-edit. Last valid list still emitted
        // from previous keystroke (before the JSON became invalid) is
        // what the parent will send if invoke happens now.
      }
    }
    return out;
  }

  bool _listsEqual(List a, List b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (jsonEncode(a[i]) != jsonEncode(b[i])) return false;
    }
    return true;
  }

  void _addItem() {
    setState(() {
      _items.add(_ArrayItem(id: _nextId++, ctrl: TextEditingController()));
    });
  }

  void _removeItem(_ArrayItem item) {
    setState(() {
      item.ctrl.dispose();
      _items.remove(item);
    });
    _emit();
  }

  void _emit() {
    final l = _currentList();
    _lastEmitted = l;
    widget.onChanged(l);
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < _items.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.only(top: 12, right: 6),
                  child: Text(
                    '${i + 1}.',
                    style: const TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 12,
                    ),
                  ),
                ),
                Expanded(
                  child: TextFormField(
                    controller: _items[i].ctrl,
                    maxLines: null,
                    minLines: 2,
                    decoration: InputDecoration(
                      hintText: widget.itemHint,
                      border: const OutlineInputBorder(),
                      isDense: true,
                      contentPadding: const EdgeInsets.all(8),
                    ),
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 12,
                    ),
                    onChanged: (_) => _emit(),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.close, size: 18),
                  tooltip: 'Remove',
                  onPressed: () => _removeItem(_items[i]),
                ),
              ],
            ),
          ),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            icon: const Icon(Icons.add, size: 16),
            label: const Text('Add item'),
            onPressed: _addItem,
          ),
        ),
      ],
    );
  }
}

class _ArrayItem {
  final int id;
  final TextEditingController ctrl;
  _ArrayItem({required this.id, required this.ctrl});
}