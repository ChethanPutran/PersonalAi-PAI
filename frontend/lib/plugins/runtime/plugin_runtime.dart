import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

class PluginRuntime {
  static const _tag = '[PluginRuntime]';
  static const _channel = MethodChannel('pai/plugin_runtime');

  // ---------------------------------------------------------------
  // Load / unload
  // ---------------------------------------------------------------

  Future<Map<String, dynamic>> loadNativeModule({
  required String nativeModule,
  required String artifactPath,
  required String entrypoint,
  required List<String> capabilities,
  required List<String> permissions,
  String type = 'native', 
}) async {
  debugPrint('$_tag loadNativeModule:');
  debugPrint('$_tag   nativeModule = $nativeModule');
  debugPrint('$_tag   artifactPath = $artifactPath');
  debugPrint('$_tag   type         = $type');

   final module = nativeModule.trim();
  final artifact = artifactPath.trim();

  if (module.isEmpty) {
    return {
      'ok': false,
      'code': 'INVALID_ARGS',
      'error': 'nativeModule cannot be empty',
    };
  }

  if (artifact.isEmpty) {
    return {
      'ok': false,
      'code': 'INVALID_ARGS',
      'error': 'artifactPath cannot be empty',
    };
  }

  if (!await File(artifact).exists()) {
    return {
      'ok': false,
      'code': 'ARTIFACT_NOT_FOUND',
      'error': 'Native artifact does not exist: $artifact',
    };
  }

  try {
    final r = await _channel.invokeMethod<Map<dynamic, dynamic>>(
      'loadNativeModule',
      {
        'nativeModule': nativeModule,
        'artifactPath': artifactPath,
        'entrypoint': entrypoint,
        'capabilities': capabilities,
        'permissions': permissions,
        'type': type,             
      },
    );

    final map = (r ?? const <dynamic, dynamic>{}).cast<String, dynamic>();
    if (map.isEmpty) {
      return {'ok': false, 'error': 'empty_response_from_native'};
    }

    // Normalise: Linux sends CSV, Android sends List.
    List<String> asList(dynamic v) {
      if (v == null) return const [];
      if (v is List) return List<String>.from(v);
      if (v is String) {
        return v.isEmpty ? const [] : v.split(',').map((s) => s.trim()).toList();
      }
      return const [];
    }

    if (map['denied'] != null) {
      map['denied'] = asList(map['denied']);
    }
    if (map['permissions'] != null) {
      map['permissions'] = asList(map['permissions']);
    }
    if (map['capabilities'] != null) {
      map['capabilities'] = asList(map['capabilities']);
    }
    if (map['denied'] != null) {
  map['denied'] = asList(map['denied']);
}
if (map['permissions'] != null) {
  map['permissions'] = asList(map['permissions']);
}
if (map['capabilities'] != null) {
  map['capabilities'] = asList(map['capabilities']);
}
    // Surface pending as a distinct error so the caller can prompt + retry.
    if (map['error'] == 'permissions_pending') {
      return {
        'ok': false,
        'error': 'permissions_pending',
        'name': map['name'] ?? '',
        'pending': map['pending'] ?? '',
      };
    }

    debugPrint('$_tag loadNativeModule($nativeModule) → $map');
    return map;
  } on MissingPluginException catch (e) {
    debugPrint('$_tag loadNativeModule → MissingPluginException: $e');
    return {'ok': false, 'error': 'native_channel_missing'};
  } on PlatformException catch (e) {
    debugPrint('$_tag loadNativeModule → PlatformException: '
        'code=${e.code} message=${e.message}');
    return {
      'ok': false,
      'error': e.message ?? 'platform_exception',
      'code': e.code,
    };
  } catch (e, st) {
    debugPrint('$_tag loadNativeModule → unexpected: $e');
    debugPrintStack(stackTrace: st);
    return {'ok': false, 'error': e.toString()};
  }
}

Future<void> grantPermissions({
  required String nativeModule,
  required List<String> permissions,
}) async {
  await _channel.invokeMethod('grantPermissions', {
    'nativeModule': nativeModule,
    'permissions': permissions,
  });
}

Future<void> denyPermissions({
  required String nativeModule,
  required List<String> permissions,
}) async {
  await _channel.invokeMethod('denyPermissions', {
    'nativeModule': nativeModule,
    'permissions': permissions,
  });
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

  // PluginRuntime — replace body
Future<String> getAbi() async {
  if (!Platform.isAndroid) {
    // Native platforms: read from Platform.operatingSystemVersion
    // if needed; on Linux/Windows desktop x86_64 is the norm.
    return Platform.version.contains('arm64') ? 'arm64' : 'x86_64';
  }
  try {
    return await _channel.invokeMethod<String>('getAbi') ?? 'unknown';
  } catch (e) {
    return 'unknown';
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

  Future<Map<String, dynamic>> debugRegistry() async {
  try {
    final r = await _channel.invokeMethod<Map<dynamic, dynamic>>('debugRegistry');
    return (r ?? const {}).cast<String, dynamic>();
  } catch (e) {
    debugPrint('$_tag debugRegistry error: $e');
    return {};
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
  'ok': false,
  'error': e.message ?? 'platform_exception',
};
    } catch (e) {
      debugPrint('$_tag requestPermissions unexpected: $e');
      return {'ok': 'false', 'error': e.toString()};
    }
  }
}
