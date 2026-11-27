import 'dart:convert';
import 'dart:io';

import 'package:archive/archive.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/documents/extractors.dart';

const _xml = '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
    '<w:p><w:r><w:t>Hello &amp; welcome</w:t></w:r></w:p>'
    '<w:p><w:r><w:t xml:space="preserve">Tab</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>here &#65;&#x42;</w:t></w:r></w:p>'
    '<w:p><w:r><w:instrText> HYPERLINK "http://evil" </w:instrText></w:r><w:r><w:t>Line</w:t><w:br/><w:t>break</w:t></w:r></w:p>'
    '</w:body></w:document>';

List<int> buildDocx(String xml, {String name = 'word/document.xml'}) {
  final a = Archive();
  a.addFile(ArchiveFile.string('[Content_Types].xml', '<Types/>'));
  a.addFile(ArchiveFile(name, utf8.encode(xml).length, utf8.encode(xml)));
  return ZipEncoder().encode(a);
}

void main() {
  late Directory tmp;
  setUp(() => tmp = Directory.systemTemp.createTempSync('docx'));
  tearDown(() => tmp.deleteSync(recursive: true));

  test('docxXmlToText keeps paragraphs, drops field codes, decodes entities', () {
    final t = docxXmlToText(_xml);
    expect(t, contains('Hello & welcome\n'));
    expect(t, contains('Tab\there AB'));
    expect(t, contains('Line\nbreak'));
    expect(t, isNot(contains('HYPERLINK')));
    expect(t, isNot(contains('<')));
  });

  test('a docx file is read through the zip', () {
    final f = File('${tmp.path}/a.docx')..writeAsBytesSync(buildDocx(_xml));
    expect(extractTextSync(f.path), contains('Hello & welcome'));
  });

  test('a docx without word/document.xml, or garbage, gives null (no throw)', () {
    final f = File('${tmp.path}/b.docx')..writeAsBytesSync(buildDocx(_xml, name: 'other.xml'));
    expect(extractTextSync(f.path), isNull);
    final g = File('${tmp.path}/c.docx')..writeAsStringSync('this is not a zip');
    expect(extractTextSync(g.path), isNull);
  });

  test('text types are read (bad UTF-8 tolerated); other types are not', () {
    for (final e in ['txt', 'md', 'csv', 'log', 'json', 'rst']) {
      final f = File('${tmp.path}/x.$e')..writeAsBytesSync([...utf8.encode('hi '), 0xFF, ...utf8.encode(' there')]);
      expect(extractTextSync(f.path), contains('there'), reason: e);
    }
    final p = File('${tmp.path}/x.pdf')..writeAsStringSync('%PDF-1.4');
    expect(extractTextSync(p.path), isNull);
    expect(kPdfSupportNote, 'PDF not supported on this phone yet');
  });

  test('extractChunksInIsolate chunks a file and returns null for unsupported ones', () async {
    final f = File('${tmp.path}/n.txt')..writeAsStringSync(List.generate(300, (i) => 'Sentence $i here.').join(' '));
    final chunks = await extractChunksInIsolate(f.path, maxChunks: 3);
    expect(chunks, isNotNull);
    expect(chunks!.length, 3);
    final p = File('${tmp.path}/n.bin')..writeAsStringSync('x');
    expect(await extractChunksInIsolate(p.path, maxChunks: 3), isNull);
  });
}
