import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/ui/widgets/markdown/markdown_safety.dart';

void main() {
  group('parseSafeLink', () {
    final allowed = <String>[
      'https://example.com',
      'http://example.com/path?q=1#frag',
      'HTTPS://EXAMPLE.COM/UP',
      'mailto:someone@example.com',
      'https://sub.example.co.uk:8443/a_b',
    ];
    for (final u in allowed) {
      test('allows $u', () => expect(parseSafeLink(u), isNotNull));
    }

    final denied = <String, String>{
      'javascript': 'javascript:alert(1)',
      'javascript mixed case': 'JaVaScRiPt:alert(1)',
      'javascript with leading space/newline tricks': ' \njavascript:alert(1)',
      'embedded tab in scheme': 'java\tscript:alert(1)',
      'data': 'data:text/html;base64,PHNjcmlwdD4=',
      'file': 'file:///etc/passwd',
      'intent': 'intent://scan/#Intent;scheme=zxing;end',
      'content': 'content://com.android.contacts/contacts',
      'tel': 'tel:+15551234567',
      'sms': 'sms:+15551234567',
      'vbscript': 'vbscript:msgbox(1)',
      'custom app scheme': 'myapp://transfer?to=evil',
      'ftp': 'ftp://example.com/x',
      'no scheme (relative)': '/relative/path',
      'protocol-relative': '//evil.example/x',
      'empty': '',
      'whitespace only': '   ',
      'http without host': 'https:///path',
      'credentials look-alike': 'https://google.com@evil.example/',
      'space inside': 'https://exam ple.com',
      'control char': 'https://example.com/\u0000',
      'mailto without address': 'mailto:',
    };
    for (final e in denied.entries) {
      test('rejects ${e.key}', () => expect(parseSafeLink(e.value), isNull));
    }

    test('null and absurdly long urls are rejected', () {
      expect(parseSafeLink(null), isNull);
      expect(parseSafeLink('https://example.com/${'a' * 5000}'), isNull);
    });
  });

  group('parseSafeImage', () {
    test('only http(s) with a host', () {
      expect(parseSafeImage(Uri.parse('https://cdn.example.com/a.png')), isNotNull);
      expect(parseSafeImage(Uri.parse('http://cdn.example.com/a.png')), isNotNull);
      for (final u in ['file:///etc/passwd', 'data:image/png;base64,AAAA', 'ftp://x/y.png', 'content://a/b']) {
        expect(parseSafeImage(Uri.parse(u)), isNull, reason: u);
      }
      expect(parseSafeImage(Uri.parse('https://u:p@evil.example/a.png')), isNull);
    });
  });

  test('hasUnusualHost flags non-ASCII (look-alike) hosts', () {
    expect(hasUnusualHost(Uri.parse('https://example.com')), isFalse);
    expect(hasUnusualHost(Uri.parse('https://exаmple.com')), isTrue); // Cyrillic "а"
    expect(displayHost(Uri.parse('https://exаmple.com')), 'exаmple.com'); // shown decoded, not %D0%B0
  });

  group('boundMarkdown', () {
    test('clamps blockquote nesting', () {
      final out = boundMarkdown('${'> ' * 500}deep');
      expect('>'.allMatches(out.split('\n').first).length, lessThanOrEqualTo(const MarkdownLimits().maxBlockquoteDepth));
      expect(out, contains('deep'));
    });

    test('clamps indentation (deep list nesting) but not inside code fences', () {
      final out = boundMarkdown('${' ' * 80}- deep item\n```\n${' ' * 40}keep me\n```');
      final lines = out.split('\n');
      expect(lines[0].length - lines[0].trimLeft().length, const MarkdownLimits().maxIndent);
      expect(lines[2], '${' ' * 40}keep me');
    });

    test('caps table rows and says so', () {
      final rows = List.generate(500, (i) => '| r$i | x |').join('\n');
      final out = boundMarkdown('| a | b |\n|---|---|\n$rows');
      expect('\n| r'.allMatches(out).length, const MarkdownLimits().maxTableRows);
      expect(out, contains('more rows not shown'));
    });

    test('caps table columns', () {
      final header = '| ${List.generate(60, (i) => 'c$i').join(' | ')} |';
      final sep = '|${List.filled(60, '---').join('|')}|';
      final out = boundMarkdown('$header\n$sep\n$header');
      final first = out.split('\n').first;
      expect('|'.allMatches(first).length - 1, const MarkdownLimits().maxTableCols);
    });

    test('plain text and normal tables are untouched', () {
      const text = 'Hello\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- x\n  - y';
      expect(boundMarkdown(text), text);
    });
  });

  group('escapeRawHtml', () {
    final cases = <String, String>{
      '<b>x</b>': r'\<b>x\</b>',
      "<script>alert('x')</script>": r"\<script>alert('x')\</script>",
      '<img src="http://e/x.png" onerror="a()">': r'\<img src="http://e/x.png" onerror="a()">',
      '<!-- hidden -->': r'\<!-- hidden -->',
      'see <https://example.com/a> now': 'see <https://example.com/a> now',
      'mail <me@example.com>': 'mail <me@example.com>',
      '`<b>` stays': '`<b>` stays',
      'if a < b and c<d': r'if a < b and c\<d', // "\<" still displays as "<"; only look-alike tags are escaped
      '```\n<b>code</b>\n```': '```\n<b>code</b>\n```',
      r'already \<escaped>': r'already \<escaped>',
      'no angle brackets': 'no angle brackets',
    };
    for (final e in cases.entries) {
      test(e.key.replaceAll('\n', '\\n'), () => expect(escapeRawHtml(e.key), e.value));
    }
  });

  test('truncateAtLineBoundary prefers a line break near the limit', () {
    final text = '${'x' * 90}\n${'y' * 50}';
    expect(truncateAtLineBoundary(text, 100), 'x' * 90);
    expect(truncateAtLineBoundary('short', 100), 'short');
    expect(truncateAtLineBoundary('z' * 300, 100).length, 100);
  });

  group('stripMarkdown (voice captions)', () {
    final cases = <String, String>{
      '**bold** and *italic* and ~~gone~~': 'bold and italic and gone',
      '# Heading\n\nbody': 'Heading\n\nbody',
      'a [link](https://x.y/z) b': 'a link b',
      'pic ![chart](https://x.y/c.png) end': 'pic chart end',
      '- one\n- two\n1. three': 'one\ntwo\nthree',
      '> quoted': 'quoted',
      'use `code` here': 'use code here',
      '```py\nprint(1)\n```': 'print(1)',
      '| a | b |\n|---|---|\n| 1 | 2 |': 'a b\n\n1 2',
      '<b>html</b> text': 'html text',
      'snake_case_word stays': 'snake_case_word stays',
      '**partial bold': 'partial bold',
      'plain sentence.': 'plain sentence.',
    };
    for (final e in cases.entries) {
      test(e.key.replaceAll('\n', '\\n'), () => expect(stripMarkdown(e.key), e.value));
    }
    test('never throws', () => expect(stripMarkdown('*' * 20000 + '[' * 20000), isA<String>()));
  });
}
