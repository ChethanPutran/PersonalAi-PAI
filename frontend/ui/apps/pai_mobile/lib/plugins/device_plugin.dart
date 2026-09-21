import 'models/plugin_info.dart';

class DevicePlugin {
  final PluginInfo info;
  bool enabled;
  final DateTime installedAt;

  DevicePlugin({
    required this.info,
    this.enabled = false,
    DateTime? installedAt,
  }) : installedAt = installedAt ?? DateTime.now();

  String get id => info.id;
}