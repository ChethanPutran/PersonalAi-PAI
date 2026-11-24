import 'package:flutter/material.dart';

import 'pai_tokens.dart';

/// Font families bundled under `assets/fonts` (declared in pubspec.yaml; all SIL OFL).
///
///  * Orbitron      — display: brand, big state labels, numerals
///  * Rajdhani      — HUD labels, headings, buttons (condensed, techy, very legible)
///  * Inter         — body text and form fields
///  * JetBrains Mono — ids, timestamps, logs
abstract final class PaiFonts {
  static const display = 'Orbitron';
  static const hud = 'Rajdhani';
  static const body = 'Inter';
  static const mono = 'JetBrainsMono';
}

abstract final class PaiText {
  /// Material text theme tuned for the HUD look. [color] is the default text colour.
  static TextTheme theme(Color color, {Color? muted}) {
    final m = muted ?? color.withValues(alpha: 0.75);
    TextStyle hud(double size, FontWeight w, {double ls = 0.4, Color? c}) => TextStyle(
        fontFamily: PaiFonts.hud, fontSize: size, fontWeight: w, letterSpacing: ls,
        height: 1.15, color: c ?? color);
    TextStyle body(double size, {FontWeight w = FontWeight.w400, Color? c}) => TextStyle(
        fontFamily: PaiFonts.body, fontSize: size, fontWeight: w, height: 1.4, color: c ?? color);
    return TextTheme(
      displayLarge: const TextStyle(fontFamily: PaiFonts.display, fontSize: 40, fontWeight: FontWeight.w700, letterSpacing: 2).copyWith(color: color),
      displayMedium: const TextStyle(fontFamily: PaiFonts.display, fontSize: 32, fontWeight: FontWeight.w700, letterSpacing: 1.6).copyWith(color: color),
      displaySmall: const TextStyle(fontFamily: PaiFonts.display, fontSize: 24, fontWeight: FontWeight.w600, letterSpacing: 1.4).copyWith(color: color),
      headlineLarge: hud(32, FontWeight.w700, ls: 0.8),
      headlineMedium: hud(26, FontWeight.w700, ls: 0.8),
      headlineSmall: hud(22, FontWeight.w600, ls: 0.8),
      titleLarge: hud(21, FontWeight.w600, ls: 0.9),
      titleMedium: hud(18, FontWeight.w600, ls: 0.7),
      titleSmall: hud(16, FontWeight.w600, ls: 0.7),
      labelLarge: hud(16, FontWeight.w600, ls: 1.0),
      labelMedium: hud(14, FontWeight.w600, ls: 1.1),
      labelSmall: hud(12.5, FontWeight.w600, ls: 1.2, c: m),
      bodyLarge: body(16),
      bodyMedium: body(14.5),
      bodySmall: body(12.5, c: m),
    );
  }

  /// Monospace style for ids / timestamps / logs.
  static TextStyle mono({double size = 12, Color? color}) => TextStyle(
      fontFamily: PaiFonts.mono, fontSize: size, color: color ?? PaiColors.textMid, height: 1.4);
}
