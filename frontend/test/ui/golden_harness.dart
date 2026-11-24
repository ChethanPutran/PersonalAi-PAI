import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/ui/theme/theme.dart';

/// Fixed wall clock for every UI test (see PaiClock).
final DateTime testNow = DateTime(2026, 11, 23, 12, 0, 0);

/// Loads the bundled fonts (and the Material icon font) so golden images show the real
/// typography instead of the test-runner's boxy fallback.
Future<void> loadPaiFonts() async {
  TestWidgetsFlutterBinding.ensureInitialized();
  Future<void> load(String family, List<String> files) async {
    final loader = FontLoader(family);
    for (final f in files) {
      loader.addFont(Future.value(ByteData.sublistView(Uint8List.fromList(File(f).readAsBytesSync()))));
    }
    await loader.load();
  }

  await load('Orbitron', ['assets/fonts/Orbitron-Variable.ttf']);
  await load('Rajdhani', [
    'assets/fonts/Rajdhani-Medium.ttf',
    'assets/fonts/Rajdhani-SemiBold.ttf',
    'assets/fonts/Rajdhani-Bold.ttf',
  ]);
  await load('Inter', ['assets/fonts/Inter-Variable.ttf']);
  await load('JetBrainsMono', ['assets/fonts/JetBrainsMono-Variable.ttf']);
  final root = Platform.environment['FLUTTER_ROOT'] ?? '/home/chethan/Desktop/flutter_sdk/flutter';
  final icons = File('$root/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf');
  if (icons.existsSync()) await load('MaterialIcons', [icons.path]);
}

const phone = Size(360, 740);
const desktop = Size(960, 600);

/// Pumps [home] inside the real PAI theme, with animations disabled so the frame is stable.
Future<void> pumpHud(
  WidgetTester t,
  Widget home, {
  Size size = phone,
  ThemeMode mode = ThemeMode.dark,
  double textScale = 1.0,
}) async {
  PaiClock.now = () => testNow;
  addTearDown(() => PaiClock.now = DateTime.now);
  t.view.physicalSize = size;
  t.view.devicePixelRatio = 1.0;
  addTearDown(t.view.reset);
  await t.pumpWidget(MaterialApp(
    debugShowCheckedModeBanner: false,
    theme: PaiTheme.light(),
    darkTheme: PaiTheme.dark(),
    themeMode: mode,
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(
        disableAnimations: true,
        textScaler: TextScaler.linear(textScale),
      ),
      child: child!,
    ),
    home: home,
  ));
  await t.pump(const Duration(milliseconds: 150));
}

/// Compares the whole app against `test/goldens/<name>.png`.
Future<void> expectGolden(WidgetTester t, String name) =>
    expectLater(find.byType(MaterialApp), matchesGoldenFile('../goldens/$name.png'));
