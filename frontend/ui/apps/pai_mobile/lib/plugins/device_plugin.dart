import 'models/plugin_info.dart';

abstract class DevicePlugin {
  PluginInfo get info;

  Future<void> install();

  Future<void> enable();

  Future<void> disable();

  Future<dynamic> execute(
    String action,
    Map<String, dynamic> params,
  );
}