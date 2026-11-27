import 'dart:convert';
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:archive/archive_io.dart';

import 'chunker.dart';

/// File types the phone can read text from. PDF is NOT here: no Dart-only, permissively licensed PDF text
/// extractor is bundled yet (see [kPdfSupportNote]).
const Set<String> kTextExtensions = {'txt', 'md', 'csv', 'log', 'json', 'rst'};
const Set<String> kDocumentExtensions = {...kTextExtensions, 'docx'};

/// Shown in the Document search status line.
const String kPdfSupportNote = 'PDF not supported on this phone yet';

/// At most this much text is read from one file (more than enough for the per-file chunk cap).
const int kMaxTextBytes = 4 * 1024 * 1024;

/// A docx part is refused when it would unzip to more than this (zip bomb guard).
const int kMaxDocxXmlBytes = 16 * 1024 * 1024;

String extensionOf(String path) {
  final name = path.substring(path.lastIndexOf('/') + 1);
  final i = name.lastIndexOf('.');
  return i <= 0 ? '' : name.substring(i + 1).toLowerCase();
}

/// Plain text of the file at [path], or null when the type is not supported / unreadable / has no text.
/// Synchronous and CPU/IO bound: run it in an isolate ([extractInIsolate]) so the UI never waits for it.
String? extractTextSync(String path) {
  final ext = extensionOf(path);
  try {
    if (kTextExtensions.contains(ext)) return _readText(path);
    if (ext == 'docx') return docxText(path);
  } catch (_) {
    return null;                       // corrupt / unreadable: skip the file, never fail the run
  }
  return null;
}

String _readText(String path) {
  final f = File(path).openSync();
  try {
    final n = f.lengthSync() < kMaxTextBytes ? f.lengthSync() : kMaxTextBytes;
    final bytes = f.readSync(n);
    return utf8.decode(bytes, allowMalformed: true);
  } finally {
    f.closeSync();
  }
}

/// The text of a Word document: its `word/document.xml` with the tags removed (paragraph ends become line
/// breaks). The file is only unzipped as text; nothing in it is executed or rendered.
String? docxText(String path) {
  final input = InputFileStream(path);
  try {
    final archive = ZipDecoder().decodeStream(input);
    for (final f in archive.files) {
      if (f.isFile && f.name == 'word/document.xml') {
        if (f.size > kMaxDocxXmlBytes) return null;
        final Uint8List bytes = f.content;
        return docxXmlToText(utf8.decode(bytes, allowMalformed: true));
      }
    }
    return null;
  } finally {
    input.closeSync();
  }
}

/// WordprocessingML -> text (no XML parser needed: only the run text matters).
String docxXmlToText(String xml) {
  var s = xml
      .replaceAll(RegExp(r'<w:instrText[^>]*>.*?</w:instrText>', dotAll: true), '')
      .replaceAll(RegExp(r'</w:p>'), '\n')
      .replaceAll(RegExp(r'<w:(tab|ptab)\b[^>]*/>'), '\t')
      .replaceAll(RegExp(r'<w:(br|cr)\b[^>]*/>'), '\n')
      .replaceAll(RegExp(r'</w:tc>'), '\t');
  s = s.replaceAll(RegExp(r'<[^>]*>'), '');
  return _unescape(s);
}

String _unescape(String s) => s.replaceAllMapped(RegExp(r'&(#x[0-9a-fA-F]+|#[0-9]+|amp|lt|gt|quot|apos);'), (m) {
      final e = m[1]!;
      switch (e) {
        case 'amp':
          return '&';
        case 'lt':
          return '<';
        case 'gt':
          return '>';
        case 'quot':
          return '"';
        case 'apos':
          return "'";
      }
      final code = e.startsWith('#x') ? int.tryParse(e.substring(2), radix: 16) : int.tryParse(e.substring(1));
      if (code == null || code <= 0 || code > 0x10FFFF || (code >= 0xD800 && code <= 0xDFFF)) return ' ';
      return String.fromCharCode(code);
    });

/// Extracts and chunks [path] in a short-lived isolate that is killed after [timeout] (a pathological file
/// cannot hold the app). Returns null when there is no text, the file is unsupported or the time ran out.
Future<List<String>?> extractChunksInIsolate(
  String path, {
  required int maxChunks,
  Duration timeout = const Duration(seconds: 30),
  TextChunker chunker = const TextChunker(),
}) async {
  final port = ReceivePort();
  Isolate? iso;
  try {
    iso = await Isolate.spawn<List<Object>>(
      _entry,
      [port.sendPort, path, maxChunks, chunker.maxChars, chunker.overlapChars],
      onError: port.sendPort,
      debugName: 'pai-doc-extract',
    );
    final r = await port.first.timeout(timeout, onTimeout: () => null);
    if (r is Map && r['chunks'] is List) return List<String>.from(r['chunks'] as List);
    return null;
  } catch (_) {
    return null;
  } finally {
    iso?.kill(priority: Isolate.immediate);
    port.close();
  }
}

void _entry(List<Object> a) {
  final out = a[0] as SendPort;
  try {
    final text = extractTextSync(a[1] as String);
    if (text == null) {
      out.send(null);
      return;
    }
    final chunks = TextChunker(maxChars: a[3] as int, overlapChars: a[4] as int).chunk(text, maxChunks: a[2] as int);
    out.send({'chunks': chunks});
  } catch (_) {
    out.send(null);
  }
}
