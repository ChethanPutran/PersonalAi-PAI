import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/plugins/permissions/permission_models.dart';

import 'fakes.dart';

void main() {
  group('ConfirmationRequest parsing', () {
    test('reads the full contract', () {
      final r = ConfirmationRequest.fromJson(confirmationJson(reason: 'Takes a photo.'));
      expect(r.capability, 'camera.capture');
      expect(r.pluginId, 'camera');
      expect(r.deviceId, 'pixel-8');
      expect(r.risk, ConfirmationRisk.medium);
      expect([for (final p in r.parametersSummary) '${p.name}=${p.valuePreview}'], ['camera=front', 'flash=false']);
      expect(r.allowedScopes, ConfirmationScope.values);
      expect(r.canBindParams, isTrue);
      expect(r.defaultBinding, ParamBinding.exact);
      expect(r.reason, 'Takes a photo.');
    });

    test('missing fields fall back to the most restrictive reading', () {
      final r = ConfirmationRequest.fromJson({'capability': 'x.y'});
      expect(r.allowedScopes, [ConfirmationScope.once]);
      expect(r.canBindParams, isFalse);
      expect(r.risk, ConfirmationRisk.high); // unknown risk is shown cautiously
      expect(r.parametersSummary, isEmpty);
      expect(r.reason, isNull);
    });

    test('unknown scopes are ignored, once is always present, duplicates collapse', () {
      final r = ConfirmationRequest.fromJson({
        'capability': 'x.y',
        'allowed_scopes': ['forever', 'ALWAYS', 'always', 'session', 7, null],
      });
      expect(r.allowedScopes, [ConfirmationScope.once, ConfirmationScope.always, ConfirmationScope.session]);
    });

    test('garbage input does not throw', () {
      expect(ConfirmationRequest.fromJson(null, fallbackCapability: 'camera.capture').capability, 'camera.capture');
      expect(ConfirmationRequest.fromJson('nope').capability, 'unknown action');
      final r = ConfirmationRequest.fromJson({
        'capability': 5,
        'parameters_summary': ['plain', 3, {'name': 'a'}, {'value': 'v'}],
        'default_binding': 'bad',
        'allowed_scopes': 'once',
      });
      expect(r.capability, '5');
      expect(r.parametersSummary.length, 4);
      expect(r.allowedScopes, [ConfirmationScope.once]);
      expect(r.defaultBinding, ParamBinding.exact);
    });

    test('restrictedTo drops disallowed scopes but keeps once', () {
      final r = sampleRequest().restrictedTo({ConfirmationScope.session});
      expect(r.allowedScopes, [ConfirmationScope.once, ConfirmationScope.session]);
      expect(sampleRequest().restrictedTo({}).allowedScopes, [ConfirmationScope.once]);
    });

    test('legacy chat events (no confirmation block) become a once-only request', () {
      final r = ConfirmationRequest.legacy(
        capability: 'files.delete',
        parameters: {'path': '/tmp/a', 'long': 'x' * 300},
        reason: 'Deletes a file',
      );
      expect(r.allowedScopes, [ConfirmationScope.once]);
      expect(r.canBindParams, isFalse);
      expect(r.reason, 'Deletes a file');
      expect(r.parametersSummary.first.name, 'path');
      expect(r.parametersSummary.last.valuePreview.length, lessThanOrEqualTo(120)); // truncated
    });
  });

  group('request body for POST /tasks/{id}/confirm', () {
    String j(ConfirmationDecision d) => jsonEncode(d.toBody());

    test('once', () => expect(j(const ConfirmationDecision.once()), '{"scope":"once"}'));

    test('session, exact params (default)', () {
      expect(j(const ConfirmationDecision.session()),
          '{"scope":"session","binding":{"mode":"exact","keys":[]}}');
    });

    test('session, only some keys', () {
      expect(
        j(const ConfirmationDecision.session(
            binding: ParamBinding(mode: BindingMode.keys, keys: ['path', 'name']))),
        '{"scope":"session","binding":{"mode":"keys","keys":["path","name"]}}',
      );
    });

    test('session, any params', () {
      expect(j(const ConfirmationDecision.session(binding: ParamBinding.any)),
          '{"scope":"session","binding":{"mode":"any","keys":[]}}');
    });

    test('always defaults to exact params and 90 days', () {
      expect(j(const ConfirmationDecision.always()),
          '{"scope":"always","binding":{"mode":"exact","keys":[]},"days":90}');
    });

    test('always, any params, one year', () {
      expect(j(const ConfirmationDecision.always(binding: ParamBinding.any, days: 365)),
          '{"scope":"always","binding":{"mode":"any","keys":[]},"days":365}');
    });

    test('when parameters cannot be bound the binding is omitted', () {
      expect(j(const ConfirmationDecision.session(binding: null)), '{"scope":"session"}');
      expect(j(const ConfirmationDecision.always(binding: null, days: 30)), '{"scope":"always","days":30}');
    });

    test('once never carries a binding or days', () {
      expect(const ConfirmationDecision.once().toBody().keys, ['scope']);
    });
  });

  group('ConfirmationException.fromBody', () {
    test('structured detail', () {
      final e = ConfirmationException.fromBody(
          409, {'detail': {'code': 'control_not_supported', 'message': 'Not allowed to remember this.'}});
      expect((e.code, e.message, e.statusCode), ('control_not_supported', 'Not allowed to remember this.', 409));
    });
    test('plain string detail', () => expect(ConfirmationException.fromBody(422, {'detail': 'bad scope'}).message, 'bad scope'));
    test('FastAPI validation list', () {
      final e = ConfirmationException.fromBody(422, {'detail': [{'msg': 'value is not a valid enumeration member'}]});
      expect(e.message, contains('valid enumeration'));
    });
    test('unreadable body still gives a message', () {
      expect(ConfirmationException.fromBody(409, null).message, contains('409'));
      expect(ConfirmationException.fromBody(500, 'html').message, isNotEmpty);
    });
  });

  group('ConsentGrant', () {
    final full = {
      'id': 'c1', 'scope': 'always', 'plugin_id': 'camera', 'capability': 'camera.capture',
      'device_id': 'pixel-8', 'device_name': 'Pixel 8',
      'binding': {'mode': 'exact', 'keys': []}, 'params_preview': 'camera=front',
      'created_at': '2026-11-01T10:00:00Z', 'expires_at': '2026-12-20T10:00:00Z',
      'last_used_at': '2026-11-23T11:55:00Z', 'use_count': 4, 'risk': 'medium',
    };

    test('parses the contract', () {
      final g = ConsentGrant.tryParse(full)!;
      expect(g.scope, ConfirmationScope.always);
      expect(g.deviceLabel, 'Pixel 8');
      expect(g.bindingSummary, 'exact params: camera=front');
      expect(g.useCount, 4);
      expect(g.expiresAt, DateTime.parse('2026-12-20T10:00:00Z'));
      expect(g.risk, ConfirmationRisk.medium);
    });

    test('binding summaries', () {
      ConsentGrant with_(Map<String, dynamic> b, {String? preview}) =>
          ConsentGrant.tryParse({...full, 'binding': b, 'params_preview': preview})!;
      expect(with_({'mode': 'any'}).bindingSummary, 'any params');
      expect(with_({'mode': 'keys', 'keys': ['path', 'name']}).bindingSummary, 'keys: path, name');
      expect(with_({'mode': 'exact'}).bindingSummary, 'exact params');
    });

    test('tolerates missing fields; unknown scope is shown as the broader one; no id => skipped', () {
      final g = ConsentGrant.tryParse({'id': 'c2', 'scope': 'weird'})!;
      expect(g.scope, ConfirmationScope.always);
      expect(g.capability, 'unknown');
      expect(g.deviceLabel, 'any device');
      expect(g.useCount, 0);
      expect(g.expiresAt, isNull);
      expect(ConsentGrant.tryParse({'scope': 'session'}), isNull);
      expect(ConsentGrant.tryParse('x'), isNull);
      expect(ConsentGrant.tryParse({...full, 'use_count': 'seven', 'expires_at': 'not a date'})!.useCount, 0);
    });
  });

  group('preflight / invoke / task parsing', () {
    test('preflight decisions; unknown or unreadable => deny', () {
      expect(PreflightResult.fromJson({'decision': 'allow'}).decision, PreflightDecision.allow);
      final c = PreflightResult.fromJson({'decision': 'confirm', 'confirmation': confirmationJson()});
      expect(c.decision, PreflightDecision.confirm);
      expect(c.confirmation!.capability, 'camera.capture');
      expect(PreflightResult.fromJson({'decision': 'maybe'}).decision, PreflightDecision.deny);
      expect(PreflightResult.fromJson(null).decision, PreflightDecision.deny);
      expect(PreflightResult.fromJson({'decision': 'deny', 'reason': 'blocked'}).reason, 'blocked');
    });

    test('invoke outcome', () {
      final o = InvokeOutcome.fromJson({
        'task_id': 't1', 'status': 'awaiting_confirmation', 'awaiting_confirmation': true,
        'confirmation': confirmationJson(),
      });
      expect(o.awaitingConfirmation, isTrue);
      expect(o.finished, isFalse);
      expect(o.confirmation!.allowedScopes.length, 3);
      final done = InvokeOutcome.fromJson({'task_id': 't2', 'status': 'completed', 'result': {'ok': 1}});
      expect(done.finished, isTrue);
      expect(InvokeOutcome.fromJson('junk').taskId, isNull);
    });

    test('task run state: nested task or flat; result.output; failures', () {
      final ok = TaskRunState.fromJson({
        'task': {'status': 'completed', 'result': {'success': true, 'output': {'path': '/x.jpg'}}},
      });
      expect(ok.succeeded, isTrue);
      expect(ok.output, {'path': '/x.jpg'});
      final failed = TaskRunState.fromJson({'status': 'failed', 'error': 'camera busy'});
      expect((failed.terminal, failed.succeeded, failed.error), (true, false, 'camera busy'));
      final inner = TaskRunState.fromJson({'status': 'completed', 'result': {'success': false, 'error': 'nope'}});
      expect(inner.succeeded, isFalse);
      expect(inner.error, 'nope');
      expect(TaskRunState.fromJson(null).status, 'unknown');
      expect(TaskRunState.fromJson({'status': 'running'}).terminal, isFalse);
    });
  });
}
