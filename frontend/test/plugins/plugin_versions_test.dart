import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/app_version.dart';
import 'package:pai_mobile/plugins/models/plugin_info.dart';
import 'package:pai_mobile/plugins/models/plugin_update.dart';

void main() {
  test('compareVersions orders numerically, not lexically', () {
    expect(compareVersions('1.10.0', '1.9.0'), greaterThan(0));
    expect(compareVersions('2.0', '2.0.0'), 0);
    expect(compareVersions('1.0.0+3', '1.0.0'), 0);
    expect(compareVersions('garbage', '0.0.1'), lessThan(0));
    expect(isNewerVersion('2.0.0', '1.1.0'), isTrue);
    expect(isNewerVersion('1.0.0', '1.0.0'), isFalse);
    expect(isNewerVersion('x', '1.0.0'), isFalse);
  });

  test('appIncompatibility gates on minAppVersion', () {
    expect(appIncompatibility(null), isNull);
    expect(appIncompatibility('1.0.0', appVersion: '1.0.0'), isNull);
    expect(appIncompatibility('1.4.0', appVersion: '1.0.0'), contains('1.4.0'));
  });

  test('PluginUpdate parses backend payload', () {
    final u = PluginUpdate.fromJson({
      'plugin_id': 'camera_linux',
      'installed_version': '1.0.0',
      'latest_version': '2.0.0',
      'update_available': true,
      'blocked': {'version': '3.0.0', 'code': 'runtime_too_old', 'reason': 'x', 'required': '99.0.0'},
      'changelog': [
        {'version': '2.0.0', 'summary': 's', 'features': ['Live streaming'], 'fixes': [], 'breaking': []}
      ],
      'capabilities': {'added': ['camera.stream'], 'removed': [], 'changed': []},
    });
    expect(u.updateAvailable, isTrue);
    expect(u.addedCapabilities, ['camera.stream']);
    expect(u.changelog.single.features, ['Live streaming']);
    expect(u.blocked!.required, '99.0.0');
    expect(u.hasNotes, isTrue);
  });

  test('PluginInfo carries version metadata and compat flags', () {
    final p = PluginInfo.fromJson({
      'id': 'camera_linux', 'name': 'Camera', 'version': '2.0.0', 'type': 'native',
      'runtimeKind': 'device',
      'runtimeSpec': {'kind': 'device', 'nativeModule': 'pai.camera', 'minAppVersion': '1.4.0'},
      'changelog': {'summary': 'streaming', 'features': ['Live streaming']},
      'schemaVersion': 2,
      'compatible': false, 'incompatibleReason': 'Requires PAI app 1.4.0',
    });
    expect(p.runtimeSpec.minAppVersion, '1.4.0');
    expect(p.changelog!.version, '2.0.0');
    expect(p.schemaVersion, 2);
    expect(p.compatible, isFalse);
  });
}
