import 'dart:io';
import '../device_plugin.dart';
import '../runtime/plugin_runtime.dart';
import '../plugin_installer.dart';

class PlatformPluginRegistration {
  final PluginRuntime runtime;
  final PluginInstaller installer;
  PlatformPluginRegistration(this.runtime, this.installer);

  Future<bool> register(DevicePlugin plugin) async {
    final platform = _platformKey();
    final spec = plugin.info.platforms[platform];
    if (spec == null) return false;

    final dir = await installer.pluginDir(plugin.info.id, plugin.info.version);
    final artifactPath = '${dir.path}/${spec.artifact}';
    if (!File(artifactPath).existsSync()) return false;

    return runtime.loadNativeModule(
      nativeModule: plugin.info.runtime.nativeModule,
      artifactPath: artifactPath,
      entrypoint: spec.entrypoint,
      capabilities: plugin.info.capabilities.map((c) => c.id).toList(),
      permissions: plugin.info.permissions,
    );
  }

  Future<void> unregister(DevicePlugin plugin) =>
      runtime.unloadNativeModule(plugin.info.runtime.nativeModule);

  String _platformKey() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isLinux)   return 'linux';
    if (Platform.isWindows) return 'windows';
    if (Platform.isMacOS)   return 'macos';
    if (Platform.isIOS)     return 'ios';
    throw UnsupportedError('unknown platform');
  }
}