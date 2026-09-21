import 'plugin_command_router.dart';

class PluginCommandHandler {
  final PluginCommandRouter router;

  PluginCommandHandler({
    required this.router,
  });

  Future<Map<String, dynamic>> handle(
    Map<String, dynamic> message,
  ) async {
    try {
      final result =
          await router.handle(message);

      return {
        'success': true,
        'result': result,
        'error': null,
      };
    } catch (e) {
      return {
        'success': false,
        'result': {
          'operation':
              message['type'],
          'plugin_id':
              message['plugin_id'] ??
                  _pluginIdFromPayload(message),
        },
        'error': e.toString(),
      };
    }
  }

  String? _pluginIdFromPayload(
    Map<String, dynamic> message,
  ) {
    final plugin =
        message['plugin'];

    if (plugin is Map) {
      return plugin['id']?.toString();
    }

    return null;
  }
}