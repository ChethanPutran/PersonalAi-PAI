import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/device/remote_access.dart';

class _Api extends ApiClient {
  _Api() : super(baseUrl: 'http://test', apiPrefix: '/api/v1');
  final puts = <String, Map<String, dynamic>>{};
  Object? failWith;
  Map<String, dynamic> reply = {'allow_remote_install': false, 'allow_remote_invoke': false};

  @override
  Future<dynamic> put(String path, Map<String, dynamic> body) async {
    if (failWith != null) throw failWith!;
    puts[path] = body;
    return reply;
  }

  @override
  Future<dynamic> get(String path, {Map<String, String>? query}) async => reply;
}

void main() {
  group('RemoteAccess is fail-closed', () {
    test('nothing is allowed until the backend says so', () {
      expect(RemoteAccess.none.install, isFalse);
      expect(RemoteAccessController().value, RemoteAccess.none);
      expect(RemoteAccessController().known, isFalse);
    });

    test('only an exact true allows; garbage never widens access', () {
      expect(RemoteAccess.fromJson({'install': true, 'invoke': false}).install, isTrue);
      expect(RemoteAccess.fromJson({'install': 'true', 'invoke': 1}), RemoteAccess.none);
      expect(RemoteAccess.fromJson('x'), RemoteAccess.none);
      expect(RemoteAccess.fromJson(null), RemoteAccess.none);
      final c = RemoteAccessController()..applyFrame({'install': true, 'invoke': true});
      c.applyFrame('garbage');
      expect(c.value, const RemoteAccess(install: true, invoke: true)); // a bad frame is ignored
    });

    test('REST and WebSocket shapes both parse', () {
      expect(RemoteAccess.fromJson({'allow_remote_install': true, 'allow_remote_invoke': true}),
          const RemoteAccess(install: true, invoke: true));
    });
  });

  group('remote install refusal', () {
    String? refuse(String? by, RemoteAccess a) =>
        remoteInstallRefusal(requestedBy: by, thisDeviceId: 'B', access: a);

    test('a request from another device or from nowhere is refused while the switch is off', () {
      expect(refuse('A', RemoteAccess.none), kRemoteInstallOffMessage);
      expect(refuse(null, RemoteAccess.none), kRemoteInstallOffMessage);
      expect(refuse('', RemoteAccess.none), kRemoteInstallOffMessage);
    });
    test('allowed once the owner turned it on, and the invoke switch does not count', () {
      expect(refuse('A', const RemoteAccess(install: true)), isNull);
      expect(refuse('A', const RemoteAccess(invoke: true)), kRemoteInstallOffMessage);
    });
    test('this device acting on itself is local and always allowed', () {
      expect(refuse('B', RemoteAccess.none), isNull);
    });
  });

  group('remote invoke refusal', () {
    String? refuse(String? src, RemoteAccess a, {String cap = 'camera.capture'}) =>
        remoteInvokeRefusal(capability: cap, sourceDeviceId: src, thisDeviceId: 'B', access: a);

    test('refused for other devices, the web, schedules and agents while off', () {
      expect(refuse('A', RemoteAccess.none), kRemoteUseOffMessage);
      expect(refuse(null, RemoteAccess.none), kRemoteUseOffMessage);
    });
    test('allowed with the switch on; own requests are always local', () {
      expect(refuse('A', const RemoteAccess(invoke: true)), isNull);
      expect(refuse('B', RemoteAccess.none), isNull);
    });
    test('the install switch does not allow remote use', () {
      expect(refuse('A', const RemoteAccess(install: true)), kRemoteUseOffMessage);
    });
    test('server-scheduled reminders are not plugin use and keep working', () {
      expect(refuse(null, RemoteAccess.none, cap: 'system.notifications.show'), isNull);
    });
  });

  group('RemoteAccessController', () {
    test('a frame from the backend applies at once (the owner changed it elsewhere)', () {
      final c = RemoteAccessController();
      var notified = 0;
      c.addListener(() => notified++);
      c.applyFrame({'install': true, 'invoke': false});
      expect(c.known, isTrue);
      expect(c.value.install, isTrue);
      expect(notified, greaterThan(0));
    });

    test('saving shows the value the BACKEND returns, not the one asked for', () async {
      final api = _Api()..reply = {'allow_remote_install': false, 'allow_remote_invoke': true};
      final c = RemoteAccessController(api: api, deviceId: 'B');
      await c.setInstall(true); // the server answers "install still off"
      expect(api.puts['/devices/B/remote-access'], {'allow_remote_install': true});
      expect(c.value, const RemoteAccess(install: false, invoke: true));
      expect(c.error, isNull);
      expect(c.saving, isFalse);
    });

    test('a failed save keeps the old value and says so in plain words', () async {
      final api = _Api()..failWith = ApiException(500, {'detail': 'boom'});
      final c = RemoteAccessController(api: api, deviceId: 'B')..applyFrame({'install': true});
      await c.setInstall(false);
      expect(c.value.install, isTrue);
      expect(c.error, isNot(contains('boom')));
      expect(c.error, isNotNull);
    });

    test('reset goes back to fail-closed', () {
      final c = RemoteAccessController()..applyFrame({'install': true, 'invoke': true});
      c.reset();
      expect(c.value, RemoteAccess.none);
      expect(c.known, isFalse);
    });
  });

  group('X-PAI-Device-Id', () {
    test('is sent once the device is registered and not before', () {
      final api = ApiClient(baseUrl: 'http://test', apiPrefix: '/api/v1');
      expect(api.authHeaders.containsKey(sourceDeviceHeader), isFalse);
      api.deviceId = 'dev-1';
      expect(api.authHeaders[sourceDeviceHeader], 'dev-1');
      api.deviceId = null; // logout
      expect(api.authHeaders.containsKey(sourceDeviceHeader), isFalse);
    });
  });
}
