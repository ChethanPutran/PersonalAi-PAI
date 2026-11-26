import 'package:flutter/material.dart';

import '../theme/theme.dart';

/// A list of short words typed in one at a time (likes, interests ...): type, press enter or comma,
/// and it becomes a chip; tap the x on a chip to remove it. Case-insensitive duplicates are ignored.
class ChipInput extends StatefulWidget {
  final String label;
  final String hint;
  final List<String> values;
  final ValueChanged<List<String>> onChanged;
  final int maxItems;
  final int maxLength;

  const ChipInput({
    super.key,
    required this.label,
    required this.values,
    required this.onChanged,
    this.hint = 'Add one…',
    this.maxItems = 30,
    this.maxLength = 60,
  });

  @override
  State<ChipInput> createState() => _ChipInputState();
}

class _ChipInputState extends State<ChipInput> {
  final _text = TextEditingController();
  String? _note;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  void _commit(String raw) {
    // A pasted "a, b, c" adds three.
    final parts = raw.split(',').map((s) => s.trim()).where((s) => s.isNotEmpty);
    var values = List<String>.of(widget.values);
    String? note;
    for (final part in parts) {
      final word = part.length > widget.maxLength ? part.substring(0, widget.maxLength) : part;
      if (values.any((v) => v.toLowerCase() == word.toLowerCase())) continue;
      if (values.length >= widget.maxItems) {
        note = 'At most ${widget.maxItems} ${widget.label.toLowerCase()}.';
        break;
      }
      values = [...values, word];
    }
    _text.clear();
    setState(() => _note = note);
    if (values.length != widget.values.length) widget.onChanged(values);
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(widget.label, style: text.labelLarge),
        const SizedBox(height: PaiSpacing.xs),
        if (widget.values.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: PaiSpacing.xs),
            child: Wrap(
              spacing: 6,
              runSpacing: 4,
              children: [
                for (final v in widget.values)
                  InputChip(
                    key: ValueKey('chip-${widget.label}-$v'),
                    label: Text(v),
                    visualDensity: VisualDensity.compact,
                    onDeleted: () => widget.onChanged([...widget.values.where((x) => x != v)]),
                  ),
              ],
            ),
          ),
        TextField(
          controller: _text,
          maxLength: null,
          textInputAction: TextInputAction.done,
          decoration: InputDecoration(
            isDense: true,
            hintText: widget.hint,
            suffixIcon: IconButton(
              tooltip: 'Add',
              icon: const Icon(Icons.add),
              onPressed: () => _commit(_text.text),
            ),
          ),
          onSubmitted: _commit,
          onChanged: (v) {
            if (v.endsWith(',')) _commit(v);
          },
        ),
        if (_note != null)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(_note!, style: text.bodySmall?.copyWith(color: scheme.error)),
          ),
      ],
    );
  }
}
