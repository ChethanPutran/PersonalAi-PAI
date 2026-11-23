import 'package:pai_mobile/plugins/models/plugin_capability.dart';
import 'package:pai_mobile/plugins/models/plugin_info.dart';
import 'package:pai_mobile/plugins/models/plugin_state.dart';

class DevicePlugin {
  final PluginInfo info;
  final List<PluginCapability> capabilities;
  final DateTime installedAt;

  PluginState state;
  String? error;

  DevicePlugin({
    required this.info,
    required this.capabilities,
    this.state = PluginState.disabled,
    this.error,
    DateTime? installedAt,
  }) : installedAt = installedAt ?? DateTime.now();

  String get id => info.id;
  String get version => info.version;

  bool get enabled => state == PluginState.enabled;

  set enabled(bool value) {
    state = value ? PluginState.enabled : PluginState.disabled;
  }
}