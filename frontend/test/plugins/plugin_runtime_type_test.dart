import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/plugins/runtime/plugin_runtime.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('wireLanguage turns the Dart enum string into the name the native side matches', () {
    expect(wireLanguage('PluginLanguage.kotlin'), 'kotlin');
    expect(wireLanguage('kotlin'), 'kotlin');
    expect(wireLanguage('PluginLanguage.native'), 'native');
    expect(wireLanguage(' Kotlin '), 'kotlin');
  });

  test('a Kotlin plugin is sent to the Android loader as "kotlin", never "PluginLanguage.kotlin"', () async {
    final dir = await Directory.systemTemp.createTemp('plugin_rt');
    final dex = File('${dir.path}/plugin.dex')..writeAsBytesSync([0x64, 0x65, 0x78, 0x0a]);
    Map<Object?, Object?>? sent;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('pai/plugin_runtime'),
      (call) async {
        sent = call.arguments as Map<Object?, Object?>;
        return <String, dynamic>{'ok': true, 'name': 'pai.camera', 'capabilities': ['camera.capture']};
      },
    );
    addTearDown(() async {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(const MethodChannel('pai/plugin_runtime'), null);
      await dir.delete(recursive: true);
    });

    final r = await PluginRuntime().loadNativeModule(
      nativeModule: 'pai.camera',
      artifactPath: dex.path,
      entrypoint: 'com.pai.plugins.camera.CameraPlugin',
      capabilities: const ['camera.capture'],
      permissions: const ['camera'],
      type: 'PluginLanguage.kotlin',
    );
    expect(r['ok'], true);
    expect(sent!['type'], 'kotlin');
  });
}
