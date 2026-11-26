import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/plugins/models/device_plugin.dart';
import 'package:pai_mobile/plugins/models/plugin_info.dart';
import 'package:pai_mobile/plugins/models/plugin_state.dart';
import 'package:pai_mobile/plugins/plugin_installer.dart';
import 'package:pai_mobile/plugins/plugin_registry.dart';
import 'package:pai_mobile/plugins/plugin_repository.dart';
import 'package:pai_mobile/plugins/plugin_service.dart';
import 'package:pai_mobile/plugins/plugin_settings.dart';
import 'package:pai_mobile/plugins/runtime/plugin_runtime.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _Runtime implements PluginRuntime {
  final configured = <String, Map<String, dynamic>>{};
  bool takes = true;
  @override
  Future<bool> configurePlugin(String nativeModule, Map<String, dynamic> config) async {
    if (takes) configured[nativeModule] = config;
    return takes;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _Installer implements PluginInstaller {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _Repo implements PluginRepository {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

PluginInfo _info() => PluginInfo(
      id: 'files_android', name: 'Files', version: '1.1.0', type: PluginLanguage.kotlin, runtimeKind: RuntimeKind.device,
      runtimeSpec: const PluginRuntimeSpec(kind: RuntimeKind.device, language: PluginLanguage.kotlin, nativeModule: 'pai.files'),
      capabilities: const [],
    );

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('store round trips settings per plugin and forgets them', () async {
    final store = PluginConfigStore();
    expect(await store.get('files_android'), isNull);
    await store.save('files_android', {'readOnly': true, 'allowedPaths': ['/a']});
    expect(await store.get('files_android'), {'readOnly': true, 'allowedPaths': ['/a']});
    expect(await store.get('other'), isNull);
    await store.forget('files_android');
    expect(await store.get('files_android'), isNull);
  });

  test('applyConfig remembers the settings and pushes them to the loaded plugin', () async {
    final runtime = _Runtime();
    final registry = PluginRegistry()..add(DevicePlugin(info: _info(), capabilities: const [], state: PluginState.enabled));
    final service = PluginService(installer: _Installer(), repository: _Repo(), runtime: runtime, registry: registry);
    final ok = await service.applyConfig('files_android', {'readOnly': true});
    expect(ok, isTrue);
    expect(runtime.configured['pai.files'], {'readOnly': true});
    expect(await service.configStore.get('files_android'), {'readOnly': true});
  });

  test('a plugin that is not loaded still gets the settings at its next load', () async {
    final runtime = _Runtime()..takes = false;
    final service = PluginService(installer: _Installer(), repository: _Repo(), runtime: runtime, registry: PluginRegistry());
    expect(await service.applyConfig('files_android', {'readOnly': true}), isFalse);
    expect(await service.configStore.get('files_android'), {'readOnly': true});
  });
}
