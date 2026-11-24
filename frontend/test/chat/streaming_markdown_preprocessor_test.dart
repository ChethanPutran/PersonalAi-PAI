import 'dart:math';

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/ui/widgets/markdown/streaming_markdown_preprocessor.dart';

String close(String s) => StreamingMarkdownPreprocessor.close(s);

void main() {
  // input -> what is DISPLAYED while the reply is still streaming
  final cases = <String, (String, String)>{
    // fenced code
    'open fence': ('```dart\nvoid main() {', '```dart\nvoid main() {\n```'),
    'open fence, nothing yet': ('```', '```\n```'),
    'open tilde fence': ('~~~\nx = 1', '~~~\nx = 1\n~~~'),
    'longer fence': ('````\n```\ninner', '````\n```\ninner\n````'),
    'half-typed closing fence is not content': ('```\ncode\n``', '```\ncode\n```'),
    'closed fence untouched': ('```\na\n```', '```\na\n```'),
    'text after closed fence': ('```\na\n```\nthen **bo', '```\na\n```\nthen **bo**'),
    'half-typed opener is hidden': ('Intro\n``', 'Intro'),
    'inline triple-backtick is not a fence': ('use ```code``` here', 'use ```code``` here'),
    // inline code
    'open inline code': ('Use `foo', 'Use `foo`'),
    'inline code just opened': ('Use `', 'Use '),
    'balanced inline code': ('Use `foo` and', 'Use `foo` and'),
    'double-backtick code': ('see ``a ` b', 'see ``a ` b``'),
    // emphasis
    'open bold': ('Hello **wor', 'Hello **wor**'),
    'bold just opened': ('Hello **', 'Hello'),
    'open italic star': ('a *b', 'a *b*'),
    'open italic underscore': ('say _it', 'say _it_'),
    'open strike': ('~~gone', '~~gone~~'),
    'nested open': ('**a *b', '**a *b***'),
    'bullet star is not emphasis': ('* item', '* item'),
    'snake_case untouched': ('call snake_case_name now', 'call snake_case_name now'),
    'closed bold untouched': ('Hello **world** and', 'Hello **world** and'),
    'escaped marker untouched': (r'a \*b', r'a \*b'),
    'trailing space before closer': ('**bold ', '**bold**'),
    // links / images
    'link cut mid-url': ('see [docs](https://exa', 'see docs'),
    'link label open': ('see [do', 'see do'),
    'image cut mid-url': ('pic ![alt](http', 'pic '),
    'image alt open': ('pic ![al', 'pic '),
    'complete link untouched': ('see [docs](https://x.y) now', 'see [docs](https://x.y) now'),
    // tables
    'table header alone is held back': ('Intro\n\n| a | b |', 'Intro\n'),
    'table header+separator ok': ('| a | b |\n|---|---|', '| a | b |\n|---|---|'),
    'half separator holds header': ('| a | b |\n|---|', ''),
    'half row dropped': ('| a | b |\n|---|---|\n| 1 |', '| a | b |\n|---|---|'),
    'full row kept': ('| a | b |\n|---|---|\n| 1 | 2 |', '| a | b |\n|---|---|\n| 1 | 2 |'),
    // lone markers that would flash or flip the previous line
    'lone dash': ('text\n-', 'text'),
    'lone number': ('text\n1.', 'text'),
    'lone heading mark': ('text\n#', 'text'),
    'lone quote': ('text\n>', 'text'),
    'lone double dash (setext)': ('text\n--', 'text'),
    'three dashes kept (rule/setext)': ('text\n---', 'text\n---'),
    // html
    'half-typed html tag': ('hello <ima', 'hello '),
    'less-than in math': ('if a < b then', 'if a < b then'),
    // finished paragraph / nothing to fix
    'finished paragraph': ('para one\n\n', 'para one\n\n'),
    'plain text': ('Just words.', 'Just words.'),
    'empty': ('', ''),
  };

  for (final e in cases.entries) {
    test(e.key, () => expect(close(e.value.$1), e.value.$2));
  }

  test('is idempotent for every case', () {
    for (final e in cases.entries) {
      final once = close(e.value.$1);
      expect(close(once), once, reason: e.key);
    }
  });

  test('every prefix of a rich message renders without throwing and never leaves a fence open', () {
    const msg = '# Title\n\nSome **bold**, *italic*, ~~strike~~ and `code`.\n\n'
        '- item one\n- item two with [link](https://example.com/a_b)\n\n'
        '| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint("hi")\n```\n\n> quote\n\nDone.';
    for (var i = 0; i <= msg.length; i++) {
      final out = close(msg.substring(0, i));
      final fences = RegExp(r'^```', multiLine: true).allMatches(out).length;
      expect(fences.isEven, isTrue, reason: 'odd fence count at prefix $i: ${out.replaceAll('\n', '\\n')}');
    }
  });

  test('fuzz: never throws, never grows unboundedly', () {
    final r = Random(7);
    const alphabet = '*_`~[]()!|>#-\n <a1.:/';
    for (var n = 0; n < 800; n++) {
      final s = String.fromCharCodes(
          List.generate(r.nextInt(60), (_) => alphabet.codeUnitAt(r.nextInt(alphabet.length))));
      final out = close(s);
      expect(out.length, lessThan(s.length + 16), reason: s);
    }
  });
}
