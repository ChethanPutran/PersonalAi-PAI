import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/models/chat.dart';
import 'package:pai_mobile/ui/chat/chat_screen.dart';
import 'package:pai_mobile/ui/theme/theme.dart';
import 'package:pai_mobile/ui/widgets/hud/hud.dart';

import '../ui/golden_harness.dart';

double _lum(Color c) {
  double ch(double v) => v <= 0.03928 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4).toDouble();
  return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b);
}

double contrast(Color a, Color b) {
  final l1 = _lum(a), l2 = _lum(b);
  return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05);
}

Color over(Color top, Color base) => Color.alphaBlend(top, base);

void main() {
  // Pairs used by the markdown renderer, on the surfaces it is drawn on.
  for (final dark in [true, false]) {
    test('markdown text colours meet WCAG AA (4.5:1) in the ${dark ? 'dark' : 'light'} theme', () {
      final pai = PaiPalette(dark);
      final page = dark ? PaiColors.bg1 : PaiColors.lightBg;
      final bubble = dark ? over(PaiColors.bg2.withValues(alpha: 0.85), page) : PaiColors.lightSurface;
      final codeBg = over(pai.codeBg, bubble);
      final inlineCodeBg = over(pai.cyan.withValues(alpha: 0.12), bubble);

      final pairs = <String, double>{
        'body text': contrast(pai.strong, bubble),
        'muted text (quotes, strike)': contrast(pai.muted, bubble),
        'link': contrast(pai.cyan, bubble),
        'list bullet': contrast(pai.cyan, bubble),
        'code text': contrast(pai.strong, codeBg),
        'code label / copy icon': contrast(pai.muted, codeBg),
        'inline code': contrast(pai.strong, inlineCodeBg),
        'warning text': contrast(pai.amber, dark ? PaiColors.bg2 : PaiColors.lightSurface),
      };
      pairs.forEach((name, ratio) {
        expect(ratio, greaterThanOrEqualTo(4.5), reason: '$name = ${ratio.toStringAsFixed(2)}:1');
      });
    });
  }

  testWidgets('light theme: an assistant bubble is a light surface with dark text', (t) async {
    await loadPaiFonts();
    HudBackground.debugFlat = true;
    addTearDown(() => HudBackground.debugFlat = false);
    await pumpHud(
      t,
      Scaffold(
        body: ChatMessageBubble(
          message: ChatMessage(id: 'a', role: 'assistant', content: '**Readable** `code`', createdAt: DateTime(2026, 11, 23, 12)),
          streaming: false,
        ),
      ),
      mode: ThemeMode.light,
    );
    final decorated = t.widgetList<DecoratedBox>(find.byType(DecoratedBox)).where((d) {
      final dec = d.decoration;
      return dec is BoxDecoration && dec.color == PaiColors.lightSurface;
    });
    expect(decorated, isNotEmpty, reason: 'bubble must use the light surface in light mode');
  });
}
