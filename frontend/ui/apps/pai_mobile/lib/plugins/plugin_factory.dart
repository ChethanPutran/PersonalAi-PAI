import 'models/plugin_info.dart';
import 'device_plugin.dart';

typedef PluginFactory =
    DevicePlugin Function(
  PluginInfo info,
);