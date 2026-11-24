import 'package:flutter/material.dart';

import 'pai_tokens.dart';
import 'pai_typography.dart';

/// The one theme of the app. Dark HUD is primary; a light fallback exists for accessibility
/// (system light mode / high ambient light) with the same accent and shapes.
abstract final class PaiTheme {
  static ThemeData dark() => _build(
        brightness: Brightness.dark,
        scheme: const ColorScheme.dark(
          primary: PaiColors.cyan,
          onPrimary: Color(0xFF00222A),
          primaryContainer: Color(0xFF06323B),
          onPrimaryContainer: PaiColors.cyan,
          secondary: PaiColors.azure,
          onSecondary: Color(0xFF00133A),
          secondaryContainer: Color(0xFF12254A),
          onSecondaryContainer: Color(0xFFBBD2FF),
          tertiary: PaiColors.teal,
          onTertiary: Color(0xFF00201A),
          error: PaiColors.red,
          onError: Color(0xFF2B0008),
          errorContainer: Color(0xFF3A0F1B),
          onErrorContainer: Color(0xFFFFB3C0),
          surface: PaiColors.bg1,
          onSurface: PaiColors.textHigh,
          onSurfaceVariant: PaiColors.textMid,
          surfaceContainerLowest: PaiColors.bg0,
          surfaceContainerLow: PaiColors.bg1,
          surfaceContainer: PaiColors.bg2,
          surfaceContainerHigh: PaiColors.bg3,
          surfaceContainerHighest: Color(0xFF1C2D4D),
          outline: Color(0xFF3B6089),
          outlineVariant: PaiColors.line,
          scrim: Colors.black,
        ),
        scaffoldBg: PaiColors.bg0,
      );

  static ThemeData light() => _build(
        brightness: Brightness.light,
        scheme: const ColorScheme.light(
          primary: PaiColors.lightPrimary,
          onPrimary: Colors.white,
          primaryContainer: Color(0xFFC8F4FA),
          onPrimaryContainer: Color(0xFF00363D),
          secondary: Color(0xFF1F5FD6),
          tertiary: Color(0xFF00796B),
          error: Color(0xFFB3261E),
          surface: PaiColors.lightSurface,
          onSurface: PaiColors.lightText,
          onSurfaceVariant: Color(0xFF3F5468),
          surfaceContainerLowest: Colors.white,
          surfaceContainerLow: Color(0xFFF7FAFC),
          surfaceContainer: Color(0xFFEDF3F8),
          surfaceContainerHigh: Color(0xFFE3EBF2),
          surfaceContainerHighest: Color(0xFFD8E3EC),
          outline: Color(0xFF6B8299),
          outlineVariant: Color(0xFFC3D2DF),
        ),
        scaffoldBg: PaiColors.lightBg,
      );

  static ThemeData _build({
    required Brightness brightness,
    required ColorScheme scheme,
    required Color scaffoldBg,
  }) {
    final dark = brightness == Brightness.dark;
    final text = PaiText.theme(scheme.onSurface, muted: scheme.onSurfaceVariant);
    final lineColor = dark ? PaiColors.border : scheme.outlineVariant;
    final shape = RoundedRectangleBorder(borderRadius: PaiRadii.panel);
    final fieldBorder = OutlineInputBorder(
      borderRadius: BorderRadius.circular(PaiRadii.md),
      borderSide: BorderSide(color: dark ? PaiColors.line : scheme.outlineVariant),
    );

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: scaffoldBg,
      canvasColor: scheme.surface,
      textTheme: text,
      primaryTextTheme: text,
      fontFamily: PaiFonts.body,
      visualDensity: VisualDensity.standard,
      materialTapTargetSize: MaterialTapTargetSize.padded,
      splashFactory: InkSparkle.splashFactory,
      dividerTheme: DividerThemeData(color: dark ? PaiColors.line : scheme.outlineVariant, space: 1, thickness: 1),
      appBarTheme: AppBarTheme(
        backgroundColor: Colors.transparent,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        foregroundColor: scheme.onSurface,
        titleTextStyle: TextStyle(
            fontFamily: PaiFonts.display, fontSize: 17, fontWeight: FontWeight.w700,
            letterSpacing: 3, color: scheme.primary),
        iconTheme: IconThemeData(color: scheme.onSurface),
        actionsIconTheme: IconThemeData(color: scheme.onSurfaceVariant),
      ),
      cardTheme: CardThemeData(
        color: scheme.surfaceContainer.withValues(alpha: dark ? 0.82 : 1),
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: PaiRadii.panel,
          side: BorderSide(color: lineColor),
        ),
      ),
      listTileTheme: ListTileThemeData(
        iconColor: scheme.primary,
        textColor: scheme.onSurface,
        subtitleTextStyle: text.bodySmall,
        titleTextStyle: text.titleSmall,
        minVerticalPadding: 8,
        shape: shape,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: dark ? PaiColors.bg2.withValues(alpha: 0.9) : scheme.surfaceContainerLow,
        isDense: false,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
        border: fieldBorder,
        enabledBorder: fieldBorder,
        focusedBorder: fieldBorder.copyWith(
            borderSide: BorderSide(color: scheme.primary, width: 1.6)),
        errorBorder: fieldBorder.copyWith(borderSide: BorderSide(color: scheme.error)),
        focusedErrorBorder: fieldBorder.copyWith(
            borderSide: BorderSide(color: scheme.error, width: 1.6)),
        labelStyle: text.labelMedium?.copyWith(color: scheme.onSurfaceVariant),
        hintStyle: text.bodyMedium?.copyWith(color: dark ? PaiColors.textLow : scheme.outline),
        helperStyle: text.bodySmall,
        errorStyle: text.bodySmall?.copyWith(color: scheme.error),
        prefixIconColor: scheme.primary,
      ),
      filledButtonTheme: FilledButtonThemeData(style: _buttonStyle(scheme, filled: true, text: text)),
      elevatedButtonTheme: ElevatedButtonThemeData(style: _buttonStyle(scheme, filled: true, text: text)),
      outlinedButtonTheme: OutlinedButtonThemeData(style: _buttonStyle(scheme, filled: false, text: text)),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: scheme.primary,
          minimumSize: const Size(kMinTouch, kMinTouch),
          textStyle: text.labelLarge,
          shape: shape,
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          minimumSize: const Size(kMinTouch, kMinTouch),
          foregroundColor: scheme.onSurfaceVariant,
        ),
      ),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        backgroundColor: scheme.primaryContainer,
        foregroundColor: scheme.primary,
        elevation: 0,
        highlightElevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(PaiRadii.lg),
          side: BorderSide(color: scheme.primary.withValues(alpha: 0.6)),
        ),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: scheme.surfaceContainerHigh.withValues(alpha: 0.7),
        selectedColor: scheme.primaryContainer,
        side: BorderSide(color: lineColor),
        labelStyle: text.labelMedium,
        secondaryLabelStyle: text.labelMedium?.copyWith(color: scheme.primary),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(PaiRadii.sm)),
        padding: const EdgeInsets.symmetric(horizontal: 6),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: scheme.surfaceContainer,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: text.titleLarge,
        contentTextStyle: text.bodyMedium,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(PaiRadii.lg),
          side: BorderSide(color: PaiColors.borderStrong.withValues(alpha: dark ? 0.5 : 0)),
        ),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: scheme.surfaceContainer,
        surfaceTintColor: Colors.transparent,
        modalBackgroundColor: scheme.surfaceContainer,
        showDragHandle: true,
        dragHandleColor: scheme.outline,
        shape: RoundedRectangleBorder(
          borderRadius: const BorderRadius.vertical(top: Radius.circular(PaiRadii.lg)),
          side: BorderSide(color: lineColor),
        ),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: scheme.surfaceContainerHighest,
        contentTextStyle: text.bodyMedium?.copyWith(color: scheme.onSurface),
        actionTextColor: scheme.primary,
        shape: RoundedRectangleBorder(
          borderRadius: PaiRadii.panel,
          side: BorderSide(color: lineColor),
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: (dark ? PaiColors.bg1 : scheme.surface).withValues(alpha: 0.94),
        surfaceTintColor: Colors.transparent,
        indicatorColor: scheme.primaryContainer,
        height: 68,
        iconTheme: WidgetStateProperty.resolveWith((s) => IconThemeData(
            size: 24,
            color: s.contains(WidgetState.selected) ? scheme.primary : scheme.onSurfaceVariant)),
        labelTextStyle: WidgetStateProperty.resolveWith((s) => text.labelSmall!.copyWith(
            color: s.contains(WidgetState.selected) ? scheme.primary : scheme.onSurfaceVariant)),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: (dark ? PaiColors.bg1 : scheme.surface).withValues(alpha: 0.94),
        indicatorColor: scheme.primaryContainer,
        selectedIconTheme: IconThemeData(color: scheme.primary),
        unselectedIconTheme: IconThemeData(color: scheme.onSurfaceVariant),
        selectedLabelTextStyle: text.labelSmall!.copyWith(color: scheme.primary),
        unselectedLabelTextStyle: text.labelSmall,
        labelType: NavigationRailLabelType.all,
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) ? scheme.primary : scheme.outline),
        trackColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected)
            ? scheme.primaryContainer
            : scheme.surfaceContainerHighest),
        trackOutlineColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected)
            ? scheme.primary.withValues(alpha: 0.7)
            : scheme.outlineVariant),
      ),
      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) ? scheme.primary : Colors.transparent),
        checkColor: WidgetStatePropertyAll(scheme.onPrimary),
        side: BorderSide(color: scheme.outline, width: 1.4),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: scheme.primary,
        linearTrackColor: scheme.surfaceContainerHighest,
        circularTrackColor: scheme.surfaceContainerHighest,
      ),
      sliderTheme: SliderThemeData(
        activeTrackColor: scheme.primary,
        inactiveTrackColor: scheme.surfaceContainerHighest,
        thumbColor: scheme.primary,
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: SegmentedButton.styleFrom(
          textStyle: text.labelMedium,
          side: BorderSide(color: lineColor),
          selectedBackgroundColor: scheme.primaryContainer,
          selectedForegroundColor: scheme.primary,
        ),
      ),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: scheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(PaiRadii.sm),
          border: Border.all(color: lineColor),
        ),
        textStyle: text.bodySmall?.copyWith(color: scheme.onSurface),
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: scheme.surfaceContainerHigh,
        surfaceTintColor: Colors.transparent,
        textStyle: text.bodyMedium,
        shape: RoundedRectangleBorder(
          borderRadius: PaiRadii.panel, side: BorderSide(color: lineColor)),
      ),
      dropdownMenuTheme: DropdownMenuThemeData(textStyle: text.bodyMedium),
      tabBarTheme: TabBarThemeData(
        labelColor: scheme.primary,
        unselectedLabelColor: scheme.onSurfaceVariant,
        labelStyle: text.labelLarge,
        unselectedLabelStyle: text.labelLarge,
        indicatorColor: scheme.primary,
        dividerColor: dark ? PaiColors.line : scheme.outlineVariant,
      ),
    );
  }

  static ButtonStyle _buttonStyle(ColorScheme s, {required bool filled, required TextTheme text}) {
    final shape = RoundedRectangleBorder(borderRadius: BorderRadius.circular(PaiRadii.md));
    return ButtonStyle(
      minimumSize: const WidgetStatePropertyAll(Size(kMinTouch, kMinTouch)),
      padding: const WidgetStatePropertyAll(EdgeInsets.symmetric(horizontal: 20, vertical: 12)),
      textStyle: WidgetStatePropertyAll(text.labelLarge),
      shape: WidgetStatePropertyAll(shape),
      elevation: const WidgetStatePropertyAll(0),
      backgroundColor: WidgetStateProperty.resolveWith((st) {
        if (!filled) return Colors.transparent;
        if (st.contains(WidgetState.disabled)) return s.surfaceContainerHighest;
        return s.primary;
      }),
      foregroundColor: WidgetStateProperty.resolveWith((st) {
        if (st.contains(WidgetState.disabled)) return s.onSurfaceVariant.withValues(alpha: 0.6);
        return filled ? s.onPrimary : s.primary;
      }),
      side: WidgetStateProperty.resolveWith((st) {
        if (filled) return BorderSide.none;
        return BorderSide(
            color: st.contains(WidgetState.disabled)
                ? s.outlineVariant
                : s.primary.withValues(alpha: 0.65));
      }),
      overlayColor: WidgetStatePropertyAll(s.primary.withValues(alpha: 0.12)),
    );
  }
}
