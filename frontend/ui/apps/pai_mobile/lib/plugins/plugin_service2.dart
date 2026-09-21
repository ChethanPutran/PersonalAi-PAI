import 'package:flutter/foundation.dart';

import '../core/api/api_client.dart';
import 'models/plugin_info_short.dart';

class PluginService {
  final ApiClient api;

  PluginService(this.api);

  Future<List<PluginInfo>> getCatalog({
    required String platform,
    required String architecture,
  }) async {
    debugPrint(
      '[PluginService] Loading catalog '
      'platform=$platform '
      'architecture=$architecture',
    );

    final result = await api.get(
      '/plugins/catalog',
      query: {
        'platform': platform,
        'architecture': architecture,
      },
    );

    final rawPlugins = result['plugins'];

    if (rawPlugins is! List) {
      throw Exception(
        'Invalid plugin catalog response',
      );
    }

    return rawPlugins
        .map(
          (item) => PluginInfo.fromJson(
            Map<String, dynamic>.from(item),
          ),
        )
        .toList();
  }

  Future<void> installPlugin({
    required String deviceId,
    required String pluginId,
  }) async {
    debugPrint(
      '[PluginService] Installing '
      'plugin=$pluginId '
      'device=$deviceId',
    );

    await api.post(
      '/plugins/$pluginId/install',
      {
        'device_id': deviceId,
      },
    );
  }

  Future<void> enablePlugin({
    required String deviceId,
    required String pluginId,
  }) async {
    debugPrint(
      '[PluginService] Enabling '
      'plugin=$pluginId '
      'device=$deviceId',
    );

    await api.post(
      '/plugins/$pluginId/enable',
      {
        'device_id': deviceId,
      },
    );
  }

  Future<void> disablePlugin({
    required String deviceId,
    required String pluginId,
  }) async {
    debugPrint(
      '[PluginService] Disabling '
      'plugin=$pluginId '
      'device=$deviceId',
    );

    await api.post(
      '/plugins/$pluginId/disable',
      {
        'device_id': deviceId,
      },
    );
  }

  Future<List<PluginInfo>> getInstalledPlugins(
    String deviceId,
  ) async {
    final result = await api.get(
      '/devices/$deviceId/plugins',
    );

    final rawPlugins = result['plugins'];

    if (rawPlugins is! List) {
      return [];
    }

    return rawPlugins
        .map(
          (item) => PluginInfo.fromJson(
            Map<String, dynamic>.from(item),
          ),
        )
        .toList();
  }

  Future<void> syncPlugins({
    required String deviceId,
    required List<PluginInfo> plugins,
  }) async {
    debugPrint(
      '[PluginService] Syncing plugins '
      'device=$deviceId',
    );

    await api.put(
      '/devices/$deviceId/plugins',
      {
        'plugins': plugins
            .where(
              (p) =>
                  p.state == PluginState.enabled ||
                  p.state == PluginState.disabled ||
                  p.state == PluginState.installed,
            )
            .map((p) => p.toJson())
            .toList(),
      },
    );
  }
}