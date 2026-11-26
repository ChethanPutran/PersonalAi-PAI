import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/device/remote_access.dart';
import 'package:pai_mobile/plugins/remote_lifecycle.dart';

class _Rig {
  _Rig({RemoteAccess access = RemoteAccess.none}) : _access = access {
    life = RemoteLifecycle(
      thisDeviceId: () => deviceId,
      access: () => _access,
      send: (f) async => frames.add(f),
      reportInstall: ({required pluginId, required version, operationId, required success, error}) async =>
          reports.add({'kind': 'install', 'plugin': pluginId, 'version': version, 'op': operationId, 'ok': success, 'error': error}),
      reportUninstall: ({required pluginId, operationId, required success, error}) async =>
          reports.add({'kind': 'uninstall', 'plugin': pluginId, 'op': operationId, 'ok': success, 'error': error}),
      bindStages: (l) => stageListener = l,
    );
  }
  final String? deviceId = 'B';
  RemoteAccess _access;
  late final RemoteLifecycle life;
  final frames = <Map<String, dynamic>>[];
  final reports = <Map<String, dynamic>>[];
  void Function(String)? stageListener;
  set access(RemoteAccess a) => _access = a;
}

Map<String, dynamic> _install({String? by = 'A', String op = 'op1'}) => {
      'type': 'plugin.install',
      'plugin_id': 'camera',
      'version': '1.0.0',
      'operation_id': op,
      'requested_by_device': by,
    };

void main() {
  test('a remote install is refused while the switch is off, and the requester is told why', () async {
    final r = _Rig();
    final refusal = await r.life.refuseIfNotAllowed(_install());
    expect(refusal, kRemoteInstallOffMessage);
    expect(r.frames.single, {
      'type': 'plugin.progress',
      'operation_id': 'op1',
      'status': 'failed',
      'error': kRemoteInstallOffMessage,
      'error_code': 'remote_install_disabled',
    });
    expect(r.reports.single['ok'], isFalse);
    expect(r.reports.single['op'], 'op1');
  });

  test('fail closed: before the backend has told us anything, remote is refused', () async {
    final r = _Rig(); // RemoteAccess.none is what the controller holds until the first frame
    expect(await r.life.refuseIfNotAllowed(_install()), isNotNull);
  });

  test('allowed once the owner turned it on; the install switch is all it takes', () async {
    final r = _Rig(access: const RemoteAccess(install: true));
    expect(await r.life.refuseIfNotAllowed(_install()), isNull);
    expect(r.frames, isEmpty);
    expect(r.reports, isEmpty);
  });

  test('an install this device asked for itself is local', () async {
    final r = _Rig();
    expect(await r.life.refuseIfNotAllowed(_install(by: 'B')), isNull);
  });

  test('a remote uninstall needs the same switch and is reported as an uninstall', () async {
    final r = _Rig();
    final msg = {'type': 'plugin.uninstall', 'plugin_id': 'camera', 'operation_id': 'op2', 'requested_by_device': 'A'};
    expect(await r.life.refuseIfNotAllowed(msg), isNotNull);
    expect(r.reports.single['kind'], 'uninstall');
  });

  test('stages from the installer go out as plugin.progress frames for the active operation only', () async {
    final r = _Rig(access: const RemoteAccess(install: true));
    r.life.begin(_install());
    r.stageListener!('downloading');
    r.stageListener!('verifying');
    r.stageListener!('installing');
    await Future<void>.delayed(Duration.zero);
    expect(r.frames.map((f) => f['status']), ['downloading', 'verifying', 'installing']);
    expect(r.frames.every((f) => f['operation_id'] == 'op1'), isTrue);

    r.life.end();
    expect(r.stageListener, isNull); // detached: a later install cannot report on this operation
  });

  test('the permission dialog is reported as awaiting_permission, then installing again', () async {
    final r = _Rig(access: const RemoteAccess(install: true));
    r.life.begin(_install());
    await r.life.awaitingPermission();
    await r.life.permissionAnswered();
    expect(r.frames.map((f) => f['status']), ['awaiting_permission', 'installing']);
  });

  test('no operation id (an old backend) means no progress frames, and nothing breaks', () async {
    final r = _Rig(access: const RemoteAccess(install: true));
    r.life.begin({'type': 'plugin.install', 'plugin_id': 'camera', 'version': '1', 'requested_by_device': 'A'});
    expect(r.stageListener, isNull);
    await r.life.awaitingPermission();
    expect(r.frames, isEmpty);
  });

  test('the final result is reported with the operation id', () async {
    final r = _Rig(access: const RemoteAccess(install: true));
    await r.life.succeeded(_install());
    await r.life.failed(_install(op: 'op3'), 'Permission needed: camera');
    expect(r.reports[0], {'kind': 'install', 'plugin': 'camera', 'version': '1.0.0', 'op': 'op1', 'ok': true, 'error': null});
    expect(r.reports[1]['ok'], isFalse);
    expect(r.reports[1]['op'], 'op3');
    expect(r.reports[1]['error'], 'Permission needed: camera');
  });

  test('a progress frame that cannot be sent never breaks the install', () async {
    final life = RemoteLifecycle(
      thisDeviceId: () => 'B',
      access: () => const RemoteAccess(install: true),
      send: (_) async => throw StateError('socket closed'),
      reportInstall: ({required pluginId, required version, operationId, required success, error}) async {},
      reportUninstall: ({required pluginId, operationId, required success, error}) async {},
      bindStages: (_) {},
    );
    await life.progress('op1', 'downloading'); // must not throw
  });

  test('handles() covers only install and uninstall', () {
    expect(RemoteLifecycle.handles('plugin.install'), isTrue);
    expect(RemoteLifecycle.handles('plugin.uninstall'), isTrue);
    expect(RemoteLifecycle.handles('plugin.enable'), isFalse);
    expect(RemoteLifecycle.handles('invoke'), isFalse);
  });
}
