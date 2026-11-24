import 'package:flutter/material.dart';

/// Design tokens of the PAI "HUD" look. Every colour, radius, spacing and duration used by
/// the UI comes from here, so the whole app can be re-skinned in one place.
///
/// Contrast (text on [PaiColors.bg1]): textHigh ≈ 15:1, textMid ≈ 8:1, textLow ≈ 5.6:1 —
/// all ≥ WCAG AA for body text.
abstract final class PaiColors {
  // Backgrounds, darkest → lightest
  static const bg0 = Color(0xFF05080F); // app background
  static const bg1 = Color(0xFF0B1220); // screens / large surfaces
  static const bg2 = Color(0xFF101A2E); // panels
  static const bg3 = Color(0xFF16233D); // raised / hovered

  // Brand
  static const cyan = Color(0xFF00E5FF); // primary
  static const cyanDim = Color(0xFF00B8D4);
  static const azure = Color(0xFF3D8BFF); // secondary
  static const teal = Color(0xFF1DE9B6); // tertiary

  // Semantic
  static const amber = Color(0xFFFFB300); // warning / paused / retrying
  static const red = Color(0xFFFF4D6D); // error / failed
  static const green = Color(0xFF3DDC97); // success / completed

  // Text
  static const textHigh = Color(0xFFE6F4FF);
  static const textMid = Color(0xFF9FB3C8);
  static const textLow = Color(0xFF7A92AA);

  // Lines
  static const line = Color(0xFF1B3556); // quiet divider / border
  static Color get border => cyan.withValues(alpha: 0.28);
  static Color get borderStrong => cyan.withValues(alpha: 0.6);
  static Color get glow => cyan.withValues(alpha: 0.22);

  // Light fallback
  static const lightBg = Color(0xFFF2F7FB);
  static const lightSurface = Color(0xFFFFFFFF);
  static const lightPrimary = Color(0xFF00727F);
  static const lightText = Color(0xFF0B1B2B);
}

abstract final class PaiSpacing {
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 12.0;
  static const lg = 16.0;
  static const xl = 24.0;
  static const xxl = 32.0;
}

abstract final class PaiRadii {
  static const sm = 6.0;
  static const md = 10.0;
  static const lg = 14.0;
  static const pill = 999.0;
  static BorderRadius get panel => BorderRadius.circular(md);
}

abstract final class PaiDurations {
  static const fast = Duration(milliseconds: 140);
  static const medium = Duration(milliseconds: 260);
  static const slow = Duration(milliseconds: 520);
  static const ambient = Duration(seconds: 14); // background loop
}

/// Breakpoints for the responsive shell.
abstract final class PaiBreakpoints {
  static const tablet = 720.0;
  static const desktop = 1100.0;
  static bool isWide(BuildContext c) => MediaQuery.sizeOf(c).width >= tablet;
}

/// Shared text-scale-safe minimum touch target.
const double kMinTouch = 48;
