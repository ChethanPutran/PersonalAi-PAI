import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/plugins/models/device_plugin.dart';
import 'package:pai_mobile/plugins/models/plugin_info.dart';
import 'package:pai_mobile/plugins/plugin_installer.dart';
import 'package:pai_mobile/plugins/plugin_registry.dart';
import 'package:pai_mobile/plugins/plugin_repository.dart';
import 'package:pai_mobile/plugins/plugin_service.dart';
import 'package:pai_mobile/plugins/runtime/plugin_runtime.dart';

/// Records what the service asks the native side to do, in order.
class FakeRuntime implements PluginRuntime {
  final calls = <String>[];
  bool forgetThrows = false;

  @override
  Future<void> unloadNativeModule(String nativeModule) async => calls.add('unload:$nativeModule');

  @override
  Future<void> forgetPermissions(String nativeModule) async {
    calls.add('forget:$nativeModule');
    if (forgetThrows) throw StateError('native store unavailable');
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class FakeInstaller implements PluginInstaller {
  final removed = <String>[];
  final List<String> runtime;
  FakeInstaller(this.runtime);

  @override
  Future<void> remove(String id) async {
    removed.add(id);
    runtime.add('remove:$id');
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class FakeRepository implements PluginRepository {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

PluginInfo _info(String id, String module) => PluginInfo(
      id: id, name: id, version: '1.0.0', type: PluginLanguage.native, runtimeKind: RuntimeKind.device,
      runtimeSpec: PluginRuntimeSpec(kind: RuntimeKind.device, language: PluginLanguage.native, nativeModule: module),
      capabilities: const [],
    );

void main() {
  late FakeRuntime runtime;
  late FakeInstaller installer;
  late PluginRegistry registry;
  late PluginService service;

  setUp(() {
    runtime = FakeRuntime();
    installer = FakeInstaller(runtime.calls);
    registry = PluginRegistry()
      ..add(DevicePlugin(info: _info('camera_linux', 'pai.camera'), capabilities: const []))
      ..add(DevicePlugin(info: _info('files', 'pai.files'), capabilities: const []));
    service = PluginService(installer: installer, repository: FakeRepository(), runtime: runtime, registry: registry);
  });

  test('uninstalling a plugin forgets ITS permissions (and only its own), after unloading it', () async {
    await service.uninstall('camera_linux');
    expect(runtime.calls, ['unload:pai.camera', 'forget:pai.camera', 'remove:camera_linux']);
    expect(registry.byId('camera_linux'), isNull);
    expect(registry.byId('files'), isNotNull);                  // the other plugin keeps what the user allowed it
    expect(runtime.calls.where((c) => c.contains('pai.files')), isEmpty);
  });

  test('PluginRuntime.forgetPermissions sends the plugin to the native side and never throws', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    const channel = MethodChannel('pai/plugin_runtime');
    final seen = <MethodCall>[];
    var mode = 'ok';
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (call) async {
      seen.add(call);
      if (mode == 'platform') throw PlatformException(code: 'BOOM', message: 'native store unavailable');
      if (mode == 'missing') throw MissingPluginException();
      return {'ok': 'true'};
    });
    addTearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null));

    final real = PluginRuntime();
    await real.forgetPermissions('pai.files');
    expect(seen.single.method, 'forgetPermissions');
    expect(seen.single.arguments, {'nativeModule': 'pai.files'});
    mode = 'platform';
    await real.forgetPermissions('pai.files');       // does not throw
    mode = 'missing';
    await real.forgetPermissions('pai.files');       // a platform with no native store: nothing to forget
  });

  test('permission status is asked per plugin', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    const channel = MethodChannel('pai/plugin_runtime');
    final seen = <Object?>[];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (call) async {
      seen.add(call.arguments);
      return {'filesystem': 'pending'};
    });
    addTearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null));
    expect(await PluginRuntime().permissionStatus('pai.files', ['filesystem']), {'filesystem': 'pending'});
    expect(seen.single, {'nativeModule': 'pai.files', 'permissions': ['filesystem']});
  });
}
