import 'models/plugin_info_short.dart';
import 'device_plugin.dart';

typedef PluginFactory =
    DevicePlugin Function(
  PluginInfo info,
);