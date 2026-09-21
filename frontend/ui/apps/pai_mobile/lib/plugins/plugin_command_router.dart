import 'models/plugin_info_short.dart';
import 'plugin_registry.dart';

class PluginCommandRouter {
  final PluginRegistry registry;

  PluginCommandRouter({
    required this.registry,
  });

  Future<Map<String, dynamic>> handle(
    Map<String, dynamic> message,
  ) async {
    final type =
        message['type']?.toString();

    switch (type) {
      case 'plugin.install':
        return _install(message);

      case 'plugin.enable':
        return _enable(message);

      case 'plugin.disable':
        return _disable(message);

      case 'plugin.execute':
        return _execute(message);

      default:
        throw UnsupportedError(
          'Unsupported plugin command: $type',
        );
    }
  }

  Future<Map<String, dynamic>> _install(
    Map<String, dynamic> message,
  ) async {
    final rawPlugin =
        message['plugin'];

    if (rawPlugin is! Map) {
      throw ArgumentError(
        'plugin.install missing plugin metadata',
      );
    }

    final plugin = PluginInfo.fromJson(
      Map<String, dynamic>.from(
        rawPlugin,
      ),
    );

    await registry.install(plugin);

    return {
      'operation': 'plugin.install',
      'plugin_id': plugin.id,
      'version': plugin.version,
    };
  }

  Future<Map<String, dynamic>> _enable(
    Map<String, dynamic> message,
  ) async {
    final pluginId =
        message['plugin_id']?.toString();

    if (pluginId == null ||
        pluginId.isEmpty) {
      throw ArgumentError(
        'plugin.enable missing plugin_id',
      );
    }

    await registry.enable(
      pluginId,
    );

    return {
      'operation': 'plugin.enable',
      'plugin_id': pluginId,
    };
  }

  Future<Map<String, dynamic>> _disable(
    Map<String, dynamic> message,
  ) async {
    final pluginId =
        message['plugin_id']?.toString();

    if (pluginId == null ||
        pluginId.isEmpty) {
      throw ArgumentError(
        'plugin.disable missing plugin_id',
      );
    }

    await registry.disable(
      pluginId,
    );

    return {
      'operation': 'plugin.disable',
      'plugin_id': pluginId,
    };
  }

  Future<Map<String, dynamic>> _execute(
    Map<String, dynamic> message,
  ) async {
    final pluginId =
        message['plugin_id']?.toString();

    final action =
        message['action']?.toString();

    if (pluginId == null ||
        pluginId.isEmpty) {
      throw ArgumentError(
        'plugin.execute missing plugin_id',
      );
    }

    if (action == null ||
        action.isEmpty) {
      throw ArgumentError(
        'plugin.execute missing action',
      );
    }

    final rawParams =
        message['params'];

    final params =
        rawParams is Map
            ? Map<String, dynamic>.from(
                rawParams,
              )
            : <String, dynamic>{};

    final result =
        await registry.execute(
      pluginId,
      action,
      params,
    );

    return {
      'operation': 'plugin.execute',
      'plugin_id': pluginId,
      'action': action,
      'result': result,
    };
  }
}