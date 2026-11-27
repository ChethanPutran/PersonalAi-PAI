/// Splits extracted text into chunks of at most [maxChars] characters (~500 tokens), on paragraph then
/// sentence then word boundaries, with a small overlap so a sentence cut at a boundary is still found.
///
/// Pure and deterministic. The text is untrusted DATA: it is only cleaned (control characters removed), never
/// interpreted.
class TextChunker {
  final int maxChars;
  final int overlapChars;
  const TextChunker({this.maxChars = 1800, this.overlapChars = 200})
      : assert(maxChars > 0 && overlapChars >= 0);

  static final _control = RegExp(r'[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]');
  static final _para = RegExp(r'\n\s*\n');
  static final _sentence = RegExp(r'(?<=[.!?。])\s+');

  /// Text with control characters (NUL...) removed, line endings normalized and runs of blanks collapsed.
  static String clean(String text) => text
      .replaceAll('\r\n', '\n')
      .replaceAll('\r', '\n')
      .replaceAll(_control, ' ')
      .replaceAll(RegExp(r'[ \t\f]+'), ' ')
      .replaceAll(RegExp(r' ?\n ?'), '\n')
      .replaceAll(RegExp(r'\n{3,}'), '\n\n')
      .trim();

  /// At most [maxChunks] chunks (the rest of the text is dropped).
  List<String> chunk(String text, {int maxChunks = 1 << 30}) {
    final units = <String>[];
    for (final p in clean(text).split(_para)) {
      final para = p.trim();
      if (para.isEmpty) continue;
      if (para.length <= maxChars) {
        units.add(para);
        continue;
      }
      for (final s in para.split(_sentence)) {
        final sent = s.trim();
        if (sent.isEmpty) continue;
        units.addAll(sent.length <= maxChars ? [sent] : _hardSplit(sent));
      }
    }
    final out = <String>[];
    var buf = StringBuffer();
    var len = 0;
    void flush() {
      final t = buf.toString().trim();
      if (t.isNotEmpty) out.add(t);
      buf = StringBuffer();
      len = 0;
    }

    for (final u in units) {
      if (out.length >= maxChunks) break;
      if (len > 0 && len + 1 + u.length > maxChars) {
        final tail = _overlapTail(buf.toString());
        flush();
        if (tail.isNotEmpty && tail.length + 1 + u.length <= maxChars) {
          buf.write(tail);
          len = tail.length;
        }
      }
      if (len > 0) {
        buf.write('\n');
        len += 1;
      }
      buf.write(u);
      len += u.length;
    }
    if (out.length < maxChunks) flush();
    return out.length > maxChunks ? out.sublist(0, maxChunks) : out;
  }

  /// The last ~[overlapChars] characters, started at a word boundary.
  String _overlapTail(String s) {
    if (overlapChars == 0 || s.length <= overlapChars) return '';
    var tail = s.substring(s.length - overlapChars);
    final sp = tail.indexOf(RegExp(r'\s'));
    if (sp >= 0 && sp < tail.length - 1) tail = tail.substring(sp + 1);
    return tail.trim();
  }

  /// A run with no sentence end longer than [maxChars]: cut at whitespace where possible.
  List<String> _hardSplit(String s) {
    final out = <String>[];
    var i = 0;
    while (i < s.length) {
      var end = i + maxChars;
      if (end >= s.length) {
        out.add(s.substring(i));
        break;
      }
      final sp = s.lastIndexOf(RegExp(r'\s'), end);
      if (sp > i + maxChars ~/ 2) end = sp;
      out.add(s.substring(i, end).trim());
      i = end;
    }
    return out.where((e) => e.isNotEmpty).toList();
  }
}
