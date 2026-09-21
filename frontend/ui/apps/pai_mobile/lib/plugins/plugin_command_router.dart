import 'plugin_service.dart';

class PluginCommandRouter {
  final PluginService service;

  PluginCommandRouter({required this.service});

  Future<Map<String, dynamic>> handle(Map<String, dynamic> message) async {
    final type = message['type']?.toString();

    switch (type) {
      case 'plugin.install':
        final id = _requireString(message, 'plugin_id');
        final version = _requireString(message, 'version');
        await service.install(id, version);
        return {
          'operation': 'plugin.install',
          'plugin_id': id,
          'version': version,
        };

      case 'plugin.enable':
        final id = _requireString(message, 'plugin_id');
        final ok = await service.enable(id);
        if (!ok) throw StateError('Enable failed: $id');
        return {'operation': 'plugin.enable', 'plugin_id': id};

      case 'plugin.disable':
        final id = _requireString(message, 'plugin_id');
        await service.disable(id);
        return {'operation': 'plugin.disable', 'plugin_id': id};

      case 'plugin.uninstall':
        final id = _requireString(message, 'plugin_id');
        await service.uninstall(id);
        return {'operation': 'plugin.uninstall', 'plugin_id': id};

      // New style: capability-based dispatch.
      case 'capability.invoke':
        final cap = _requireString(message, 'capability');
        final params = _params(message);
        final result = await service.invoke(cap, params);
        return {
          'operation': 'capability.invoke',
          'capability': cap,
          'result': result,
        };

      // Backwards-compatible: action == capability id.
      case 'plugin.execute':
        final cap = (message['capability'] ?? message['action'])?.toString();
        if (cap == null || cap.isEmpty) {
          throw ArgumentError('plugin.execute missing capability/action');
        }
        final params = _params(message);
        final result = await service.invoke(cap, params);
        return {
          'operation': 'plugin.execute',
          'capability': cap,
          'result': result,
        };

      default:
        throw UnsupportedError('Unsupported plugin command: $type');
    }
  }

  String _requireString(Map<String, dynamic> m, String key) {
    final v = m[key]?.toString();
    if (v == null || v.isEmpty) {
      throw ArgumentError('Missing required field: $key');
    }
    return v;
  }

  Map<String, dynamic> _params(Map<String, dynamic> m) {
    final raw = m['params'] ?? m['parameters'];
    if (raw is Map) return Map<String, dynamic>.from(raw);
    return <String, dynamic>{};
  }
}