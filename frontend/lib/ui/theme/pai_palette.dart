import 'package:flutter/material.dart';

import 'pai_tokens.dart';
import 'pai_typography.dart';

/// Brightness-aware colours for things the Material [ColorScheme] has no slot for (muted/faint
/// text, hairlines, code backgrounds, status colours). Dark values are the HUD tokens; the light
/// values are darker so every text colour keeps ≥ 4.5:1 on white cards.
///
///     Text('x', style: TextStyle(color: context.pai.muted))
class PaiPalette {
  const PaiPalette(this.dark);
  final bool dark;

  Color get strong => dark ? PaiColors.textHigh : PaiColors.lightText;
  Color get muted => dark ? PaiColors.textMid : const Color(0xFF3F5468);
  Color get faint => dark ? PaiColors.textLow : const Color(0xFF51677B);
  Color get line => dark ? PaiColors.line : const Color(0xFFC3D2DF);
  Color get codeBg => dark ? Colors.black.withValues(alpha: 0.28) : const Color(0xFFEDF3F8);

  Color get cyan => dark ? PaiColors.cyan : PaiColors.lightPrimary;
  Color get teal => dark ? PaiColors.teal : const Color(0xFF00796B);
  Color get azure => dark ? PaiColors.azure : const Color(0xFF1F5FD6);
  Color get green => dark ? PaiColors.green : const Color(0xFF137A4B);
  Color get amber => dark ? PaiColors.amber : const Color(0xFF8A5A00);
  Color get red => dark ? PaiColors.red : const Color(0xFFB3261E);

  /// Monospace style (ids, timestamps, logs) in the muted, faint or strong text colour.
  TextStyle mono({double size = 12, bool faint = false, bool strong = false, Color? color}) =>
      PaiText.mono(
        size: size,
        color: color ?? (strong ? this.strong : faint ? this.faint : muted),
      );
}

extension PaiContext on BuildContext {
  PaiPalette get pai => PaiPalette(Theme.of(this).brightness == Brightness.dark);
}
