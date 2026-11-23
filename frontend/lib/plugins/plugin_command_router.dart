
import 'package:pai_mobile/plugins/models/permission_consent_handler.dart';

import 'plugin_service.dart';

/// Routes plugin-related commands to PluginService.
///
/// Supported operations:
/// - plugin.install
/// - plugin.enable
/// - plugin.disable
/// - plugin.uninstall
/// - capability.invoke
/// - plugin.execute (backward-compatible capability invocation)
///
/// This class is responsible for command validation and routing.
/// PluginService handles installation, lifecycle, and native execution.
/// Authorization policies should be enforced before capability execution.
class PluginCommandRouter {
  final PluginService service;
final PermissionConsentHandler? onPermissionConsent;

PluginCommandRouter({
  required this.service,
  this.onPermissionConsent,
});

  /// Handles an incoming plugin command and returns its result.
  ///
  /// The returned map is intended to be consumed by the command/task execution
  /// layer and included in its command result.
  Future<Map<String, dynamic>> handle(
    Map<String, dynamic> message,
  ) async {
    final type = _requireString(message, 'type');

    switch (type) {
      // ---------------------------------------------------------------------
      // Install
      // ---------------------------------------------------------------------

      case 'plugin.install': {
        final id = _requireString(message, 'plugin_id');
        final version = _requireString(message, 'version');

        final plugin = await service.install(
  id,
  version,
  onPermissionConsent: onPermissionConsent,
);

        return {
          'operation': 'plugin.install',
          'plugin_id': id,
          'version': plugin.version,
          'success': true,
        };
      }

      // ---------------------------------------------------------------------
      // Enable
      // ---------------------------------------------------------------------

      case 'plugin.enable': {
        final id = _requireString(message, 'plugin_id');

        final ok = await service.enable(
  id,
  onPermissionConsent: onPermissionConsent,
);

        if (!ok) {
          throw StateError('Enable failed: $id');
        }

        return {
          "operation": "plugin.enable",
          "plugin_id": id,
          "success": true,
        };
      }

      // ---------------------------------------------------------------------
      // Disable
      // ---------------------------------------------------------------------

      case 'plugin.disable': {
        final id = _requireString(message, 'plugin_id');

        await service.disable(id);

        return {
          'operation': 'plugin.disable',
          'plugin_id': id,
          'success': true,
        };
      }

      // ---------------------------------------------------------------------
      // Uninstall
      // ---------------------------------------------------------------------

      case 'plugin.uninstall': {
        final id = _requireString(message, 'plugin_id');

        await service.uninstall(id);

        return {
          'operation': 'plugin.uninstall',
          'plugin_id': id,
          'success': true,
        };
      }

      // ---------------------------------------------------------------------
      // Capability invocation
      // ---------------------------------------------------------------------

      /// Preferred command format:
      ///
      /// {
      ///   "type": "capability.invoke",
      ///   "capability": "camera.capture",
      ///   "params": {
      ///     "outputPath": "/tmp/photo.jpg"
      ///   }
      /// }
      case 'capability.invoke': {
        final capability = _requireString(message, 'capability');
        final params = _params(message);

        final result = await service.invoke(
          capability,
          params,
        );

        return {
          'operation': 'capability.invoke',
          'capability': capability,
          'result': result,
          'success': result['ok'] == true,
        };
      }

      // ---------------------------------------------------------------------
      // Backward-compatible capability invocation
      // ---------------------------------------------------------------------

      /// Supports legacy command format:
      ///
      /// {
      ///   "type": "plugin.execute",
      ///   "action": "camera.capture",
      ///   "params": {}
      /// }
      ///
      /// Or:
      ///
      /// {
      ///   "type": "plugin.execute",
      ///   "capability": "camera.capture",
      ///   "params": {}
      /// }
      case 'plugin.execute': {
        final capability =
            (message['capability'] ?? message['action'])?.toString().trim();

        if (capability == null || capability.isEmpty) {
          throw ArgumentError(
            'plugin.execute requires capability or action',
          );
        }

        final params = _params(message);

        final result = await service.invoke(
          capability,
          params,
        );

        return {
          'operation': 'plugin.execute',
          'capability': capability,
          'result': result,
          'success': result['ok'] == true,
        };
      }

      // ---------------------------------------------------------------------
      // Unsupported command
      // ---------------------------------------------------------------------

      default:
        throw UnsupportedError(
          'Unsupported plugin command: $type',
        );
    }
  }

  // -------------------------------------------------------------------------
  // Validation helpers
  // -------------------------------------------------------------------------

  /// Extracts a required non-empty string field.
  String _requireString(
    Map<String, dynamic> message,
    String key,
  ) {
    final value = message[key]?.toString().trim();

    if (value == null || value.isEmpty) {
      throw ArgumentError(
        'Missing required field: $key',
      );
    }

    return value;
  }

  /// Extracts capability parameters.
  ///
  /// Supports both `params` and legacy `parameters`.
  /// If both are provided, `params` takes precedence.
  ///
  /// Non-map parameter values are rejected instead of silently ignored.
  Map<String, dynamic> _params(
    Map<String, dynamic> message,
  ) {
    final raw = message['params'] ?? message['parameters'];

    if (raw == null) {
      return <String, dynamic>{};
    }

    if (raw is! Map) {
      throw ArgumentError(
        'Command params must be a JSON object',
      );
    }

    return Map<String, dynamic>.from(raw);
  }
}
