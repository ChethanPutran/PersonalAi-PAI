// Makes a PARTIAL markdown string safe to render while tokens are still arriving.
//
// A streamed reply is incomplete by nature: an open ``` fence, a `**` that has not been closed
// yet, `[label](https://exa` cut mid-URL, half a table row, a lone "-" bullet. Rendered as is,
// each of these flashes raw syntax or flips the layout (a lone "-" under a line turns it into
// a heading; a half table row breaks the table) and then "snaps" when the rest arrives.
//
// [StreamingMarkdownPreprocessor.close] returns a string for DISPLAY ONLY. The stored message
// is never modified, and once the reply is complete the exact final text is rendered instead.
// It never throws: on any internal error it returns its input unchanged.

abstract final class StreamingMarkdownPreprocessor {
  static String close(String text) {
    if (text.isEmpty) return text;
    try {
      return _close(text);
    } catch (_) {
      return text;
    }
  }
}

final RegExp _fenceLine = RegExp(r'^ {0,3}(`{3,}|~{3,})(.*)$');
final RegExp _partialFenceOpener = RegExp(r'^ {0,3}(`{1,2}|~{1,2})\s*$');
final RegExp _loneMarker = RegExp(r'^\s{0,3}(?:[-*+>]|\d{1,9}[.)]|#{1,6}|={1,2}|-{1,2})\s*$');
final RegExp _partialHtmlTag = RegExp(r'<[A-Za-z/!?][^>]*$');
final RegExp _tableSep = RegExp(r'^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$');

String _close(String text) {
  final lines = text.split('\n');

  // ---- 1. fenced code ---------------------------------------------------------------
  String? fenceChar;
  var fenceLen = 0;
  for (final line in lines) {
    final m = _fenceLine.firstMatch(line);
    if (fenceChar == null) {
      if (m == null) continue;
      final marker = m.group(1)!;
      if (marker[0] == '`' && m.group(2)!.contains('`')) continue; // inline code, not a fence
      fenceChar = marker[0];
      fenceLen = marker.length;
    } else if (m != null &&
        m.group(1)![0] == fenceChar &&
        m.group(1)!.length >= fenceLen &&
        m.group(2)!.trim().isEmpty) {
      fenceChar = null;
    }
  }
  if (fenceChar != null) {
    final body = List<String>.of(lines);
    // A half-typed closing marker ("``" inside a ``` block) is not content.
    final partial = RegExp('^ {0,3}${RegExp.escape(fenceChar)}{1,${fenceLen - 1 < 1 ? 1 : fenceLen - 1}}\$');
    if (body.length > 1 && fenceLen > 1 && partial.hasMatch(body.last) && body.last.trim().length < fenceLen) {
      body.removeLast();
    }
    var joined = body.join('\n');
    if (!joined.endsWith('\n')) joined += '\n';
    return '$joined${fenceChar * fenceLen}';
  }

  // ---- 2. everything below only concerns the LAST paragraph ---------------------------
  final ls = List<String>.of(lines);
  if (_partialFenceOpener.hasMatch(ls.last)) ls.removeLast(); // "``" on its way to "```"
  if (ls.isEmpty) return '';

  var start = ls.length;
  while (start > 0 && ls[start - 1].trim().isNotEmpty) {
    start--;
  }
  if (start == ls.length) return ls.join('\n'); // ends on a blank line: paragraph is finished
  final head = ls.sublist(0, start);
  var tail = ls.sublist(start);

  // a. lone trailing marker ("-", "1.", "#", ">", "=") — would flip the previous line / flash a bullet
  if (_loneMarker.hasMatch(tail.last)) tail.removeLast();

  // b. half-typed HTML tag at the very end
  if (tail.isNotEmpty) {
    tail[tail.length - 1] = tail.last.replaceFirst(_partialHtmlTag, '');
  }

  // c. tables
  tail = _settleTable(tail);
  if (tail.isEmpty) return head.isEmpty ? '' : head.join('\n');

  // d. inline constructs in the paragraph
  var para = tail.join('\n');
  para = _closeInlineCode(para);
  para = _fixLinks(para);
  para = _closeEmphasis(para);

  return [...head, para].join('\n');
}

List<String> _settleTable(List<String> tail) {
  var tb = tail.length;
  while (tb > 0 && tail[tb - 1].trimLeft().startsWith('|')) {
    tb--;
  }
  final block = tail.sublist(tb);
  if (block.isEmpty) return tail;

  final sepIdx = block.indexWhere(_tableSep.hasMatch);
  if (sepIdx < 0) {
    // Header seen but no separator yet: it is not a table yet. Hold it back instead of
    // showing a row of raw pipes that then "snaps" into a table.
    return tail.sublist(0, tb);
  }
  if (sepIdx == block.length - 1) {
    final headerCells = sepIdx >= 1 ? _cellCount(block[sepIdx - 1]) : -1;
    if (sepIdx < 1 || headerCells != _cellCount(block[sepIdx])) {
      // Separator still being typed (column counts differ): hold header + separator back.
      return tail.sublist(0, tb + (sepIdx >= 1 ? sepIdx - 1 : sepIdx));
    }
    return tail;
  }
  final columns = sepIdx >= 1 ? _cellCount(block[sepIdx - 1]) : _cellCount(block[sepIdx]);
  final last = block.last;
  if (!last.trimRight().endsWith('|') || _cellCount(last) < columns) {
    return tail.sublist(0, tail.length - 1); // half-typed last row: show it when complete
  }
  return tail;
}

int _cellCount(String line) {
  final t = line.trim();
  final parts = t.split('|');
  var n = parts.length;
  if (t.startsWith('|')) n--;
  if (t.endsWith('|')) n--;
  return n;
}

String _closeInlineCode(String s) {
  var open = 0;
  var openAt = -1;
  var i = 0;
  while (i < s.length) {
    if (s[i] == '\\') {
      i += 2;
      continue;
    }
    if (s[i] != '`') {
      i++;
      continue;
    }
    var j = i;
    while (j < s.length && s[j] == '`') {
      j++;
    }
    final run = j - i;
    if (open == 0) {
      open = run;
      openAt = i;
    } else if (run == open) {
      open = 0;
      openAt = -1;
    }
    i = j;
  }
  if (open == 0) return s;
  // Just opened (nothing after it): drop the marker. Otherwise close it.
  if (s.substring(openAt + open).trim().isEmpty) return s.substring(0, openAt);
  return s + '`' * open;
}

String _fixLinks(String s) {
  var out = s;
  out = out.replaceFirst(RegExp(r'!\[[^\]]*\]\([^)]*$'), ''); // image cut mid-URL: nothing yet
  out = out.replaceFirstMapped(RegExp(r'\[([^\]]*)\]\([^)]*$'), (m) => m.group(1)!); // link cut mid-URL: its label
  out = out.replaceFirst(RegExp(r'!\[[^\]]*$'), ''); // image alt still open
  out = out.replaceFirstMapped(RegExp(r'\[([^\]]*)$'), (m) => m.group(1)!); // link label still open
  return out;
}

bool _isSpace(String c) => c.trim().isEmpty;
bool _isAlnum(String c) => RegExp(r'[A-Za-z0-9]').hasMatch(c);

String _closeEmphasis(String s) {
  final stack = <String>[];
  final n = s.length;
  var i = 0;
  while (i < n) {
    final c = s[i];
    if (c == '\\') {
      i += 2;
      continue;
    }
    if (c == '`') {
      var j = i;
      while (j < n && s[j] == '`') {
        j++;
      }
      final run = j - i;
      final close = s.indexOf('`' * run, j);
      i = close < 0 ? n : close + run;
      continue;
    }
    // Runs of asterisks are handled as a unit so that "***" closes "*" first, then "**".
    if (c == '*') {
      var j = i;
      while (j < n && s[j] == '*') {
        j++;
      }
      var rem = j - i;
      final before = s.substring(0, i);
      final lineStart = before.substring(before.lastIndexOf('\n') + 1);
      final isBullet = rem == 1 && lineStart.trim().isEmpty && j < n && _isSpace(s[j]);
      if (!isBullet) {
        final prev = i > 0 ? s[i - 1] : ' ';
        final canClose = !_isSpace(prev);
        final canOpen = j >= n || !_isSpace(s[j]); // at the very end: just typed, dropped below
        while (rem > 0 && canClose && stack.isNotEmpty && stack.last.startsWith('*') && stack.last.length <= rem) {
          rem -= stack.removeLast().length;
        }
        if (rem > 0 && canOpen) {
          while (rem >= 2) {
            stack.add('**');
            rem -= 2;
          }
          if (rem == 1) stack.add('*');
        }
      }
      i = j;
      continue;
    }
    String? t;
    if (s.startsWith('~~', i)) {
      t = '~~';
    } else if (s.startsWith('__', i)) {
      t = '__';
    } else if (c == '_') {
      t = '_';
    }
    if (t == null) {
      i++;
      continue;
    }
    final prev = i > 0 ? s[i - 1] : ' ';
    final next = i + t.length < n ? s[i + t.length] : ' ';
    final underscore = t.startsWith('_');
    // A marker at the very end has just been typed: treat it as opening (it is dropped below).
    final atEnd = i + t.length >= n;
    final canOpen = (atEnd || !_isSpace(next)) && (!underscore || !_isAlnum(prev));
    final canClose = !_isSpace(prev) && (!underscore || !_isAlnum(next));
    if (stack.contains(t) && canClose) {
      stack.removeRange(stack.lastIndexOf(t), stack.length);
    } else if (canOpen) {
      stack.add(t);
    }
    i += t.length;
  }
  if (stack.isEmpty) return s;

  var out = s.trimRight();
  // A marker that was just typed with nothing after it ("Hello **") is dropped, not closed.
  while (stack.isNotEmpty && out.endsWith(stack.last)) {
    out = out.substring(0, out.length - stack.last.length).trimRight();
    stack.removeLast();
  }
  for (final t in stack.reversed) {
    out += t;
  }
  return out;
}
