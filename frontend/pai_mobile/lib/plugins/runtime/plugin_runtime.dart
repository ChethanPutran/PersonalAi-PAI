import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

class PluginRuntime {
  static const _tag = '[PluginRuntime]';
  static const _channel = MethodChannel('pai/plugin_runtime');

  // ---------------------------------------------------------------
  // Load / unload
  // ---------------------------------------------------------------

  Future<bool> loadNativeModule({
    required String nativeModule,
    required String artifactPath,
    required String entrypoint,
    required List<String> capabilities,
    required List<String> permissions,
  }) async {
    debugPrint('$_tag loadNativeModule:');
    debugPrint('$_tag   nativeModule = $nativeModule');
    debugPrint('$_tag   artifactPath = $artifactPath');
    debugPrint('$_tag   entrypoint   = $entrypoint');
    debugPrint('$_tag   capabilities = $capabilities');
    debugPrint('$_tag   permissions  = $permissions');

    try {
      final r = await _channel.invokeMethod<bool>('loadNativeModule', {
        'nativeModule': nativeModule,
        'artifactPath': artifactPath,
        'entrypoint': entrypoint,
        'capabilities': capabilities,
        'permissions': permissions,
      });

      if (r == true) {
        debugPrint('$_tag loadNativeModule($nativeModule) → true');
        return true;
      }
      debugPrint('$_tag loadNativeModule($nativeModule) → false/null');
      return false;
    } on MissingPluginException catch (e) {
      debugPrint(
          '$_tag loadNativeModule($nativeModule) → MissingPluginException: $e');
      debugPrint(
          '$_tag   the native host has no handler for "pai/plugin_runtime". '
          'Check that PluginChannel is wired into my_application.cc.');
      return false;
    } on PlatformException catch (e) {
      debugPrint('$_tag loadNativeModule($nativeModule) → PlatformException:');
      debugPrint('$_tag   code    = ${e.code}');
      debugPrint('$_tag   message = ${e.message}');
      debugPrint('$_tag   details = ${e.details}');
      return false;
    } catch (e, st) {
      debugPrint('$_tag loadNativeModule($nativeModule) → unexpected: $e');
      debugPrintStack(stackTrace: st);
      return false;
    }
  }

  Future<void> unloadNativeModule(String nativeModule) async {
    debugPrint('$_tag unloadNativeModule($nativeModule)');
    try {
      await _channel.invokeMethod('unloadNativeModule', {
        'nativeModule': nativeModule,
      });
      debugPrint('$_tag unloadNativeModule($nativeModule) ok');
    } on PlatformException catch (e) {
      debugPrint('$_tag unloadNativeModule($nativeModule) error: '
          'code=${e.code} message=${e.message}');
    }
  }

  // ---------------------------------------------------------------
  // Introspection
  // ---------------------------------------------------------------

  Future<bool> isModuleAvailable(String nativeModule) async {
    try {
      final r = await _channel.invokeMethod<bool>('isModuleAvailable', {
        'nativeModule': nativeModule,
      });
      final result = r ?? false;
      debugPrint('$_tag isModuleAvailable($nativeModule) → $result');
      return result;
    } catch (e) {
      debugPrint('$_tag isModuleAvailable($nativeModule) error: $e');
      return false;
    }
  }

  Future<List<String>> listModules() async {
    try {
      final r = await _channel.invokeMethod<List<dynamic>>('listModules');
      final list = (r ?? const []).cast<String>();
      debugPrint('$_tag listModules() → $list');
      return list;
    } catch (e) {
      debugPrint('$_tag listModules() error: $e');
      return const [];
    }
  }

  // ---------------------------------------------------------------
  // Invoke
  // ---------------------------------------------------------------

  Future<Map<String, dynamic>> invoke({
    required String nativeModule,
    required String capability,
    Map<String, dynamic> parameters = const {},
  }) async {
    debugPrint('$_tag invoke:');
    debugPrint('$_tag   nativeModule = $nativeModule');
    debugPrint('$_tag   capability   = $capability');
    debugPrint('$_tag   parameters   = $parameters');

    try {
      final r =
          await _channel.invokeMethod<Map<dynamic, dynamic>>('invoke', {
        'nativeModule': nativeModule,
        'capability': capability,
        'parameters': parameters,
      });
      final out = (r ?? const {}).cast<String, dynamic>();
      debugPrint('$_tag invoke($capability) → $out');
      return out;
    } on MissingPluginException catch (e) {
      debugPrint('$_tag invoke($capability) → MissingPluginException: $e');
      return {
        'ok': 'false',
        'error': 'native_channel_missing',
        'capability': capability,
      };
    } on PlatformException catch (e) {
      debugPrint('$_tag invoke($capability) → PlatformException:');
      debugPrint('$_tag   code    = ${e.code}');
      debugPrint('$_tag   message = ${e.message}');
      debugPrint('$_tag   details = ${e.details}');
      return {
        'ok': 'false',
        'error': e.message ?? 'platform_exception',
        'code': e.code,
        'capability': capability,
      };
    } catch (e, st) {
      debugPrint('$_tag invoke($capability) → unexpected: $e');
      debugPrintStack(stackTrace: st);
      return {
        'ok': 'false',
        'error': e.toString(),
        'capability': capability,
      };
    }
  }

  // ---------------------------------------------------------------
  // Permissions
  // ---------------------------------------------------------------

  Future<Map<String, dynamic>> requestPermissions({
    required String nativeModule,
    required List<String> permissions,
  }) async {
    debugPrint('$_tag requestPermissions('
        'nativeModule=$nativeModule, permissions=$permissions)');

    try {
      final r = await _channel
          .invokeMethod<Map<dynamic, dynamic>>('requestPermissions', {
        'nativeModule': nativeModule,
        'permissions': permissions,
      });
      final out = (r ?? const {}).cast<String, dynamic>();
      debugPrint('$_tag requestPermissions → $out');
      return out;
    } on PlatformException catch (e) {
      debugPrint('$_tag requestPermissions error: '
          'code=${e.code} message=${e.message}');
      return {
        'ok': 'false',
        'error': e.message ?? 'platform_exception',
      };
    } catch (e) {
      debugPrint('$_tag requestPermissions unexpected: $e');
      return {'ok': 'false', 'error': e.toString()};
    }
  }
}