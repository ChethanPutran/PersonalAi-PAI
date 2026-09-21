import 'package:flutter/services.dart';

class PluginRuntime {
  static const _channel = MethodChannel('pai/plugin_runtime');

  /// Native side loads a shared library / AAR and registers the module it exports.
  Future<bool> loadNativeModule({
    required String nativeModule,
    required String artifactPath,   // absolute path to .so / .aar / .dll
    required String entrypoint,
    required List<String> capabilities,
    required List<String> permissions,
  }) async {
    final r = await _channel.invokeMethod<bool>('loadNativeModule', {
      'nativeModule': nativeModule,
      'artifactPath': artifactPath,
      'entrypoint': entrypoint,
      'capabilities': capabilities,
      'permissions': permissions,
    });
    return r ?? false;
  }

  Future<void> unloadNativeModule(String nativeModule) async {
    await _channel.invokeMethod('unloadNativeModule', {'nativeModule': nativeModule});
  }

  Future<bool> isModuleAvailable(String nativeModule) async {
    final r = await _channel.invokeMethod<bool>('isModuleAvailable', {'nativeModule': nativeModule});
    return r ?? false;
  }

  Future<List<String>> listModules() async {
    final r = await _channel.invokeMethod<List<dynamic>>('listModules');
    return (r ?? const []).cast<String>();
  }

  Future<Map<String, dynamic>> invoke({
    required String nativeModule,
    required String capability,
    Map<String, dynamic> parameters = const {},
  }) async {
    final r = await _channel.invokeMethod<Map<dynamic, dynamic>>('invoke', {
      'nativeModule': nativeModule,
      'capability': capability,
      'parameters': parameters,
    });
    return (r ?? const {}).cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> requestPermissions({
    required String nativeModule,
    required List<String> permissions,
  }) async {
    final r = await _channel.invokeMethod<Map<dynamic, dynamic>>('requestPermissions', {
      'nativeModule': nativeModule,
      'permissions': permissions,
    });
    return (r ?? const {}).cast<String, dynamic>();
  }
}