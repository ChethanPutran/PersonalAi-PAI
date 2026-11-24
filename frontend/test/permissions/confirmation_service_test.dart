import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/plugins/permissions/permission_models.dart';
import 'package:pai_mobile/tasks/confirmation_service.dart';

import 'fakes.dart';

Map<String, dynamic> event({Map<String, dynamic>? confirmation, String key = 'type'}) => {
      key: 'task.confirmation_requested',
      'task_id': 't-1',
      'capability': 'camera.capture',
      'parameters': {'camera': 'front'},
      'reason': 'needs approval',
      'confirmation': ?confirmation,
    };

ConfirmationService make(FakeApi api) => ConfirmationService(api: api, sessionId: 's1');

void main() {
  test('an event with a confirmation block yields the server-provided scopes', () {
    final svc = make(FakeApi())..handleMessage(jsonEncode(event(confirmation: confirmationJson(scopes: const ['once', 'session']))));
    final p = svc.pending!;
    expect(p.taskId, 't-1');
    expect(p.request.allowedScopes, [ConfirmationScope.once, ConfirmationScope.session]);
    expect(p.request.canBindParams, isTrue);
  });

  test('an older event with no confirmation block is "once only" — never wider', () {
    final svc = make(FakeApi())..handleMessage(jsonEncode(event()));
    expect(svc.pending!.request.allowedScopes, [ConfirmationScope.once]);
    expect(svc.pending!.request.canBindParams, isFalse);
  });

  test('the "event" key is accepted as well as "type"; other events are ignored', () {
    final svc = make(FakeApi());
    svc.handleMessage(jsonEncode({'type': 'task.completed', 'task_id': 'x'}));
    expect(svc.pending, isNull);
    svc.handleMessage(jsonEncode(event(key: 'event')));
    expect(svc.pending, isNotNull);
  });

  test('garbage frames are logged and ignored', () async {
    final svc = make(FakeApi());
    final logs = await captureLogs(() async {
      svc.handleMessage('not json');
      svc.handleMessage(jsonEncode({'type': 'task.confirmation_requested'})); // no id
    });
    expect(svc.pending, isNull);
    expect(logs.join('\n'), contains('Error parsing incoming WS message'));
  });

  test('confirmWith posts the exact decision body and clears pending', () async {
    final api = FakeApi()..routes['POST /tasks/t-1/confirm'] = (_) => {'ok': true};
    final svc = make(api)..handleMessage(jsonEncode(event(confirmation: confirmationJson())));
    var notified = 0;
    svc.addListener(() => notified++);
    await svc.confirmWith(ConfirmationDecision.always(binding: ParamBinding.any, days: 30));
    expect(api.calls.single.body, {
      'scope': 'always',
      'binding': {'mode': 'any', 'keys': []},
      'days': 30,
    });
    expect(svc.pending, isNull);
    expect(notified, 1);
  });

  for (final status in [400, 403, 409, 422]) {
    test('server $status is thrown as ConfirmationException and the task stays pending', () async {
      final api = FakeApi()
        ..routes['POST /tasks/t-1/confirm'] = (_) => throw ApiException(status, {'detail': 'nope $status'});
      final svc = make(api)..handleMessage(jsonEncode(event(confirmation: confirmationJson())));
      await expectLater(
        svc.confirmWith(ConfirmationDecision.once()),
        throwsA(isA<ConfirmationException>().having((e) => e.message, 'message', contains('nope'))),
      );
      expect(svc.pending, isNotNull);
    });
  }

  test('a 500 is rethrown as-is and the task stays pending', () async {
    final api = FakeApi()..routes['POST /tasks/t-1/confirm'] = (_) => throw ApiException(500, {'detail': 'boom'});
    final svc = make(api)..handleMessage(jsonEncode(event(confirmation: confirmationJson())));
    await captureLogs(() async {
      await expectLater(svc.confirmWith(ConfirmationDecision.once()), throwsA(isA<ApiException>()));
    });
    expect(svc.pending, isNotNull);
  });

  test('confirmWith with nothing pending says so instead of posting', () async {
    final api = FakeApi();
    await expectLater(make(api).confirmWith(ConfirmationDecision.once()), throwsA(isA<ConfirmationException>()));
    expect(api.calls, isEmpty);
  });

  test('deny posts /deny and clears pending, even if the request fails', () async {
    final api = FakeApi()..routes['POST /tasks/t-1/deny'] = (_) => throw Unreachable();
    final svc = make(api)..handleMessage(jsonEncode(event(confirmation: confirmationJson())));
    await captureLogs(() => svc.deny());
    expect(api.calls.single.path, '/tasks/t-1/deny');
    expect(svc.pending, isNull);
  });
}
