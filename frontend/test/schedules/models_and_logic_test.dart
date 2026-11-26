import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/models/schedule.dart';
import 'package:pai_mobile/models/schedule_capability.dart';
import 'package:pai_mobile/schedules/param_form.dart';
import 'package:pai_mobile/schedules/schedule_service.dart';
import 'package:pai_mobile/schedules/schedule_timing.dart';

import 'fakes.dart';

Map<String, dynamic> scheduleJson([Map<String, dynamic> extra = const {}]) => {
      'id': 's1', 'user_id': 'u1', 'title': 'Morning photo', 'kind': 'cron', 'cron': '0 9 * * *',
      'timezone': 'UTC', 'capability': 'camera.capture', 'parameters': {'name': 'sunrise'},
      'catch_up': 'once', 'enabled': true, 'target_device_id': 'pixel-8',
      'created_at': '2026-11-20T10:00:00Z', 'updated_at': '2026-11-20T10:00:00Z',
      ...extra,
    };

Future<List<String>> captureLogs(Future<void> Function() body) async {
  final logs = <String>[];
  final original = debugPrint;
  debugPrint = (String? m, {int? wrapWidth}) {
    if (m != null) logs.add(m);
  };
  try {
    await body();
  } finally {
    debugPrint = original;
  }
  return logs;
}

void main() {
  group('Schedule model (new fields)', () {
    test('parses consent, last run, risk and executor', () {
      final s = Schedule.fromJson(scheduleJson({
        'requires_consent': true,
        'consent': {'active': true, 'granted_at': '2026-11-20T10:00:00Z', 'expires_at': '2026-12-20T10:00:00Z'},
        'last_run_at': '2026-11-23T09:00:00Z',
        'last_run_status': 'skipped_no_consent',
        'last_run_error': 'no standing approval',
        'risk': 'high',
        'executor': 'device_executor',
      }));
      expect(s.requiresConsent, isTrue);
      expect(s.consent.active, isTrue);
      expect(s.consent.expiresAt!.toUtc(), DateTime.utc(2026, 12, 20, 10));
      expect(s.lastRunStatus, LastRunStatus.skippedNoConsent);
      expect(s.lastRunStatus!.label, 'Skipped · needs approval');
      expect(s.lastRunError, 'no standing approval');
      expect(s.risk, ScheduleRisk.high);
      expect(s.executor, 'device_executor');
      expect(s.isReminder, isFalse);
    });

    test('an old backend (no new fields) still parses, as a reminder-like schedule', () {
      final s = Schedule.fromJson({...scheduleJson(), 'capability': 'system.notifications.show'});
      expect(s.requiresConsent, isFalse);
      expect(s.consent.active, isFalse);
      expect(s.lastRunStatus, isNull);
      expect(s.risk, ScheduleRisk.low);
      expect(s.isReminder, isTrue);
      expect(s.consentStatus(kNow), ConsentStatus.notRequired);
    });

    test('unknown / malformed values fall back instead of throwing', () {
      final s = Schedule.fromJson(scheduleJson({
        'requires_consent': 'yes-please', // not a bool -> false
        'consent': 'garbage', // not a map -> none
        'last_run_status': 'exploded', // unknown -> unknown
        'risk': 'apocalyptic', // unknown -> high (cautious)
        'last_run_at': 'not-a-date',
      }));
      expect(s.requiresConsent, isFalse);
      expect(s.consent.active, isFalse);
      expect(s.lastRunStatus, LastRunStatus.unknown);
      expect(s.risk, ScheduleRisk.high);
      expect(s.lastRunAt, isNull);
      expect(ScheduleRisk.fromWire(null), ScheduleRisk.low);
      expect(LastRunStatus.fromWire(null), isNull);
    });

    group('consent status', () {
      Schedule withConsent(ScheduleConsent c, {bool requires = true}) =>
          makeSchedule(requiresConsent: requires, consent: c);

      test('needs approval / approved / expiring soon / expired', () {
        expect(withConsent(ScheduleConsent.none).consentStatus(kNow), ConsentStatus.needsApproval);
        expect(withConsent(approvedUntil(kNow.add(const Duration(days: 30)))).consentStatus(kNow),
            ConsentStatus.approved);
        expect(withConsent(approvedUntil(kNow.add(const Duration(days: 2)))).consentStatus(kNow),
            ConsentStatus.expiringSoon);
        expect(withConsent(approvedUntil(kNow.add(const Duration(days: 3, minutes: 1)))).consentStatus(kNow),
            ConsentStatus.approved);
        expect(withConsent(approvedUntil(kNow.subtract(const Duration(hours: 1)))).consentStatus(kNow),
            ConsentStatus.expired);
        expect(withConsent(ScheduleConsent.none, requires: false).consentStatus(kNow),
            ConsentStatus.notRequired);
      });

      test('an expiry in the past is expired even if the server still says active', () {
        final lagging = ScheduleConsent(active: true, expiresAt: kNow.subtract(const Duration(minutes: 1)));
        expect(withConsent(lagging).consentStatus(kNow), ConsentStatus.expired);
        expect(ConsentStatus.expired.blocksRunning, isTrue);
        expect(ConsentStatus.needsApproval.blocksRunning, isTrue);
        expect(ConsentStatus.approved.blocksRunning, isFalse);
      });
    });
  });

  group('SchedulableCapability parsing', () {
    test('parses the contract shape', () {
      final c = SchedulableCapability.fromJson({
        'id': 'camera.capture', 'description': 'Take a photo', 'plugin_id': 'camera',
        'device_ids': ['pixel-8'], 'parameters_schema': {'type': 'object'},
        'requires_consent': true, 'risk': 'medium', 'schedulable': true, 'blocked_reason': null,
      });
      expect(c.id, 'camera.capture');
      expect(c.deviceIds, ['pixel-8']);
      expect(c.requiresConsent, isTrue);
      expect(c.risk, ScheduleRisk.medium);
      expect(c.group, 'camera');
      expect(c.isReminder, isFalse);
    });

    test('tolerates missing fields and skips malformed entries (reporting them)', () {
      final skipped = <Object?>[];
      final list = SchedulableCapability.listFromJson(
        {
          'capabilities': [
            {'id': 'a.b'}, // minimal
            'not-a-map',
            {'description': 'no id'}, // id missing -> 'null' string? must not crash
            {'id': 'blocked.one', 'schedulable': false, 'blocked_reason': 'nope', 'risk': 'weird'},
          ]
        },
        onSkip: (item, e) => skipped.add(item),
      );
      expect(list.map((c) => c.id), ['a.b', 'blocked.one']); // the id-less entry is skipped too
      expect(skipped.length, 2);
      expect(skipped, contains('not-a-map'));
      final blocked = list.firstWhere((c) => c.id == 'blocked.one');
      expect(blocked.schedulable, isFalse);
      expect(blocked.blockedReason, 'nope');
      expect(blocked.risk, ScheduleRisk.high);
      expect(SchedulableCapability.listFromJson('garbage'), isEmpty);
      expect(SchedulableCapability.listFromJson({'capabilities': 'x'}), isEmpty);
    });

    test('group falls back to the capability namespace', () {
      expect(const SchedulableCapability(id: 'files.read').group, 'files');
      expect(const SchedulableCapability(id: 'solo').group, 'solo');
    });
  });

  group('schema -> form mapping', () {
    ParamFormSpec spec(Map<String, dynamic> props, {List<String> required = const []}) =>
        ParamFormSpec.fromSchema({'type': 'object', 'properties': props, 'required': required});

    test('maps each primitive type with its constraints', () {
      final s = spec({
        'name': {'type': 'string', 'description': 'File name', 'minLength': 2, 'maxLength': 8, 'pattern': r'^[a-z]+$'},
        'count': {'type': 'integer', 'minimum': 1, 'maximum': 5, 'default': 3},
        'ratio': {'type': 'number', 'minimum': 0},
        'flash': {'type': 'boolean', 'default': true},
        'mode': {'type': 'string', 'enum': ['chime', 'alarm'], 'default': 'chime'},
      }, required: ['name']);
      final byName = {for (final f in s.fields) f.name: f};
      expect(byName['name'], isA<TextParam>());
      expect(byName['name']!.required, isTrue);
      expect(byName['name']!.description, 'File name');
      expect((byName['count'] as NumberParam).integer, isTrue);
      expect(byName['count']!.initialText, '3');
      expect((byName['ratio'] as NumberParam).integer, isFalse);
      expect((byName['flash'] as BoolParam).defaultValue, isTrue);
      expect((byName['mode'] as EnumParam).options, ['chime', 'alarm']);
      expect(byName['mode']!.initialText, 'chime');
      expect(byName['count']!.hint, '1 to 5');
      expect(byName['name']!.hint, contains('2–8 characters'));
    });

    test('validation: required, length, pattern, whole numbers, ranges, enum', () {
      final s = spec({
        'name': {'type': 'string', 'minLength': 2, 'maxLength': 8, 'pattern': r'^[a-z]+$'},
        'count': {'type': 'integer', 'minimum': 1, 'maximum': 5},
        'ratio': {'type': 'number', 'minimum': 0},
        'mode': {'type': 'string', 'enum': ['chime', 'alarm']},
      }, required: ['name', 'count']);

      var r = s.collect({'name': '', 'count': '', 'ratio': '', 'mode': ''});
      expect(r.errors, {'name': 'Required', 'count': 'Required'});

      r = s.collect({'name': 'a', 'count': '9', 'ratio': '-1', 'mode': 'siren'});
      expect(r.errors['name'], 'At least 2 characters');
      expect(r.errors['count'], 'Must be at most 5');
      expect(r.errors['ratio'], 'Must be at least 0');
      expect(r.errors['mode'], 'Choose one of the listed values');

      r = s.collect({'name': 'UPPER', 'count': '2.5', 'ratio': 'abc', 'mode': ''});
      expect(r.errors['name'], 'Does not match the required format');
      expect(r.errors['count'], 'Enter a whole number');
      expect(r.errors['ratio'], 'Enter a number');

      r = s.collect({'name': 'sunrise', 'count': '3', 'ratio': '0.5', 'mode': 'alarm'});
      expect(r.isValid, isTrue);
      expect(r.values, {'name': 'sunrise', 'count': 3, 'ratio': 0.5, 'mode': 'alarm'});
      expect(r.values['count'], isA<int>());
    });

    test('empty optional fields are omitted so server defaults apply', () {
      final s = spec({
        'a': {'type': 'string'},
        'b': {'type': 'integer'},
      });
      expect(s.collect({'a': '', 'b': ''}).values, isEmpty);
    });

    test('booleans always carry a value', () {
      final s = spec({'flash': {'type': 'boolean'}});
      expect(s.collect({'flash': 'true'}).values, {'flash': true});
      expect(s.collect({'flash': 'false'}).values, {'flash': false});
    });

    test('unsupported property shapes fall back to a validated JSON field', () {
      final s = spec({
        'tags': {'type': 'array', 'items': {'type': 'string'}},
        'opts': {'type': 'object'},
        'either': {'oneOf': [{'type': 'string'}, {'type': 'integer'}]},
        'weird': {'enum': [{'a': 1}, [1]]},
      });
      for (final f in s.fields) {
        expect(f, isA<JsonParam>(), reason: f.name);
      }
      final r = s.collect({'tags': '["a","b"]', 'opts': '{"x": 1}', 'either': '5', 'weird': ''});
      expect(r.values['tags'], ['a', 'b']);
      expect(r.values['opts'], {'x': 1});
      expect(r.values['either'], 5);
      final bad = s.collect({'tags': '{"not": "an array"}', 'opts': '[1]', 'either': '{oops', 'weird': ''});
      expect(bad.errors['tags'], contains('Expected a JSON array'));
      expect(bad.errors['opts'], contains('Expected a JSON object'));
      expect(bad.errors['either'], startsWith('Invalid JSON'));
    });

    test('a schema the form cannot map at all becomes one raw-JSON parameters field', () {
      final s = ParamFormSpec.fromSchema({'anyOf': [{'type': 'object'}, {'type': 'string'}]});
      expect(s.wholeObjectFallback, isTrue);
      expect(s.fields.single.name, ParamFormSpec.wholeObjectKey);
      expect(s.collect({ParamFormSpec.wholeObjectKey: '{"k": "v"}'}).values, {'k': 'v'});
      expect(s.collect({ParamFormSpec.wholeObjectKey: '[1]'}).errors, isNotEmpty);
      expect(s.collect({ParamFormSpec.wholeObjectKey: ''}).values, isEmpty); // empty = {}
      // free-form object with no declared properties also needs raw JSON
      expect(ParamFormSpec.fromSchema({'type': 'object', 'additionalProperties': true}).wholeObjectFallback, isTrue);
    });

    test('no schema / explicit empty properties means the action takes no parameters', () {
      expect(ParamFormSpec.fromSchema(null).isEmpty, isTrue);
      expect(ParamFormSpec.fromSchema(const {}).isEmpty, isTrue);
      final none = ParamFormSpec.fromSchema({'type': 'object', 'properties': {}});
      expect(none.isEmpty, isTrue);
      expect(none.wholeObjectFallback, isFalse);
      expect(none.collect({}).values, isEmpty);
    });

    test('edit mode prefers the schedule\'s existing values over defaults', () {
      final s = spec({
        'name': {'type': 'string', 'default': 'default-name'},
        'count': {'type': 'integer', 'default': 3},
        'tags': {'type': 'array'},
      });
      final raw = s.initialRaw({'name': 'mine', 'tags': ['x']});
      expect(raw['name'], 'mine');
      expect(raw['count'], '3'); // absent -> default
      expect(raw['tags'], '["x"]');
    });

    test('a pattern Dart cannot compile is left to the server, not a crash', () {
      final s = spec({'x': {'type': 'string', 'pattern': r'(?<n>[a-z'}});
      expect(s.collect({'x': 'abc'}).isValid, isTrue);
    });
  });

  group('server validation errors (422)', () {
    test('contract shape maps fields, parameters and banner messages', () {
      final e = ScheduleValidationException.fromBody({
        'detail': {
          'errors': [
            {'field': 'cron', 'message': 'Invalid cron expression'},
            {'field': 'parameters.name', 'message': 'name is too short'},
            {'field': 'parameters.count', 'message': 'count must be <= 5'},
            {'field': 'capability', 'message': 'Not schedulable'},
            {'field': 'something_new', 'message': 'Unexpected thing'},
            {'field': '', 'message': 'General problem'},
          ]
        }
      });
      expect(e.forField('cron'), ['Invalid cron expression']);
      expect(e.parameterErrors, {'name': 'name is too short', 'count': 'count must be <= 5'});
      const known = {'cron', 'capability', 'title'};
      expect(e.unmapped(known), ['something_new: Unexpected thing', 'General problem']);
    });

    test('also understands FastAPI\'s stock shape and a plain string', () {
      final stock = ScheduleValidationException.fromBody({
        'detail': [
          {'loc': ['body', 'interval_seconds'], 'msg': 'must be >= 10', 'type': 'greater_than_equal'},
          {'loc': ['body', 'parameters', 'path'], 'msg': 'required'},
        ]
      });
      expect(stock.forField('interval_seconds'), ['must be >= 10']);
      expect(stock.parameterErrors, {'path': 'required'});
      final plain = ScheduleValidationException.fromBody({'detail': 'Schedule rejected'});
      expect(plain.errors.single.message, 'Schedule rejected');
      final empty = ScheduleValidationException.fromBody(null);
      expect(empty.errors.single.message, isNotEmpty);
    });
  });

  group('timing', () {
    final base = DateTime(2026, 11, 23, 12, 0); // a Monday

    test('encoding keeps the existing rules', () {
      final at = DateTime(2026, 11, 25, 9, 30); // Wednesday
      Timing t(RepeatChoice r, {String cron = ''}) => Timing(repeat: r, fireAt: at, cron: cron);
      expect(encodeTiming(t(RepeatChoice.once)).kind, 'one_shot');
      expect(encodeTiming(t(RepeatChoice.once)).fireAt, at);
      expect(encodeTiming(t(RepeatChoice.hourly)).intervalSeconds, 3600);
      expect(encodeTiming(t(RepeatChoice.daily)).cron, '30 9 * * *');
      expect(encodeTiming(t(RepeatChoice.weekly)).cron, '30 9 * * 3');
      expect(encodeTiming(t(RepeatChoice.monthly)).cron, '30 9 25 * *');
      expect(encodeTiming(t(RepeatChoice.yearly)).cron, '30 9 25 11 *');
      expect(encodeTiming(t(RepeatChoice.customCron, cron: ' 5 4 * * 1 ')).cron, '5 4 * * 1');
    });

    test('decode(encode(x)) round-trips the presets', () {
      for (final cron in ['30 9 * * *', '30 9 * * 3', '30 9 25 * *', '30 9 25 11 *']) {
        final s = makeSchedule(kind: 'cron', cron: cron);
        final t = decodeTiming(s, now: base);
        expect(encodeTiming(t).cron, cron, reason: cron);
        expect(t.repeat, isNot(RepeatChoice.customCron), reason: cron);
      }
      expect(decodeTiming(makeSchedule(kind: 'cron', cron: '*/5 * * * *'), now: base).repeat,
          RepeatChoice.customCron);
      expect(decodeTiming(makeSchedule(kind: 'interval', cron: null, interval: 3600), now: base).repeat,
          RepeatChoice.hourly);
      final odd = decodeTiming(makeSchedule(kind: 'interval', cron: null, interval: 7200), now: base);
      expect(odd.repeat, RepeatChoice.keepInterval);
      expect(encodeTiming(odd).intervalSeconds, 7200); // preserved, not silently changed
      final once = decodeTiming(
          makeSchedule(kind: 'one_shot', cron: null, fireAt: DateTime(2026, 12, 1, 8, 15)), now: base);
      expect(once.repeat, RepeatChoice.once);
      expect(once.fireAt, DateTime(2026, 12, 1, 8, 15));
    });

    test('descriptions read naturally after "Runs … "', () {
      final at = DateTime(2026, 11, 25, 9, 5);
      String d(RepeatChoice r, {String cron = '0 9 * * *', int? secs}) =>
          describeTiming(Timing(repeat: r, fireAt: at, cron: cron, intervalSeconds: secs));
      expect(d(RepeatChoice.once), 'once on Nov 25, 09:05');
      expect(d(RepeatChoice.hourly), 'every hour');
      expect(d(RepeatChoice.daily), 'daily at 09:05');
      expect(d(RepeatChoice.weekly), 'every Wednesday at 09:05');
      expect(d(RepeatChoice.monthly), 'monthly on day 25 at 09:05');
      expect(d(RepeatChoice.yearly), 'every year on Nov 25 at 09:05');
      expect(d(RepeatChoice.customCron, cron: '*/5 * * * *'), 'on cron */5 * * * *');
      expect(d(RepeatChoice.keepInterval, secs: 7200), 'every 2 hours');
    });
  });

  group('ScheduleService', () {
    ScheduleService svc() => ScheduleService(api: ApiClient(baseUrl: 'http://backend.test', apiPrefix: '/api/v1'));

    Future<T> withClient<T>(MockClient c, Future<T> Function() body) =>
        http.runWithClient(body, () => c);

    test('getSchedulableCapabilities parses and skips malformed entries, logging them', () async {
      final mock = MockClient((req) async {
        expect(req.url.path, '/api/v1/schedules/capabilities');
        return http.Response(
            jsonEncode({'capabilities': [{'id': 'a.b'}, 'junk']}), 200);
      });
      late List<SchedulableCapability> caps;
      final logs = await captureLogs(() async {
        caps = await withClient(mock, () => svc().getSchedulableCapabilities());
      });
      expect(caps.map((c) => c.id), ['a.b']);
      expect(logs.join('\n'), contains('skipping malformed capability junk'));
    });

    test('misfire grace: parsed (absent = null) and sent on create / PATCH, null only when cleared', () async {
      expect(Schedule.fromJson(scheduleJson()).misfireGraceSeconds, isNull);
      expect(Schedule.fromJson(scheduleJson({'misfire_grace_seconds': 3600})).misfireGraceSeconds, 3600);

      final bodies = <Map<String, dynamic>>[];
      final mock = MockClient((req) async {
        bodies.add(jsonDecode(req.body) as Map<String, dynamic>);
        return http.Response(jsonEncode(scheduleJson()), 200);
      });
      await withClient(mock, () async {
        await svc().create(title: 't', kind: 'cron', cron: '0 9 * * *', catchUp: 'all', misfireGraceSeconds: 600);
        await svc().update('s1', misfireGraceSeconds: 300);
        await svc().update('s1', clearMisfireGrace: true);
        await svc().update('s1', title: 'x');
      });
      expect(bodies[0]['catch_up'], 'all');
      expect(bodies[0]['misfire_grace_seconds'], 600);
      expect(bodies[1]['misfire_grace_seconds'], 300);
      expect(bodies[2].containsKey('misfire_grace_seconds') && bodies[2]['misfire_grace_seconds'] == null, isTrue);
      expect(bodies[3].containsKey('misfire_grace_seconds'), isFalse);     // untouched stays untouched
    });

    test('approve posts the days; revoke deletes; both return the schedule', () async {
      final seen = <String>[];
      final mock = MockClient((req) async {
        seen.add('${req.method} ${req.url.path} ${req.body}');
        return http.Response(jsonEncode(scheduleJson({'requires_consent': true})), 200);
      });
      await withClient(mock, () async {
        final a = await svc().approve('s1', days: 7);
        expect(a.id, 's1');
        await svc().revoke('s1');
      });
      expect(seen[0], 'POST /api/v1/schedules/s1/consent {"days":7}');
      expect(seen[1], startsWith('DELETE /api/v1/schedules/s1/consent'));
    });

    test('create sends the plugin-action payload; a 422 becomes a typed exception and is logged', () async {
      Map<String, dynamic>? sent;
      final mock = MockClient((req) async {
        sent = jsonDecode(req.body) as Map<String, dynamic>;
        return http.Response(
            jsonEncode({
              'detail': {
                'errors': [{'field': 'parameters.name', 'message': 'too short'}]
              }
            }),
            422,
            headers: {'content-type': 'application/json'});
      });
      Object? thrown;
      final logs = await captureLogs(() async {
        await withClient(mock, () async {
          try {
            await svc().create(
              title: 'Photo', kind: 'cron', cron: '0 9 * * *', capability: 'camera.capture',
              targetDeviceId: 'pixel-8', parameters: {'name': 'x'},
            );
          } catch (e) {
            thrown = e;
          }
        });
      });
      expect(sent!['capability'], 'camera.capture');
      expect(sent!['parameters'], {'name': 'x'}); // NOT overwritten with the reminder text
      expect(sent!['target_device_id'], 'pixel-8');
      expect(thrown, isA<ScheduleValidationException>());
      expect((thrown as ScheduleValidationException).parameterErrors, {'name': 'too short'});
      final text = logs.join('\n');
      expect(text, contains('[ScheduleService] POST /schedules failed: status=422'));
      expect(text, contains('too short'));
    });

    test('a reminder still gets its title/body as parameters (unchanged flow)', () async {
      Map<String, dynamic>? sent;
      final mock = MockClient((req) async {
        sent = jsonDecode(req.body) as Map<String, dynamic>;
        return http.Response(jsonEncode(scheduleJson({'capability': 'system.notifications.show'})), 201);
      });
      await withClient(mock, () => svc().create(title: 'Water plants', body: 'now', kind: 'interval', intervalSeconds: 3600));
      expect(sent!['capability'], 'system.notifications.show');
      expect(sent!['parameters'], {'title': 'Water plants', 'body': 'now'});
    });

    test('update sends only the given fields', () async {
      Map<String, dynamic>? sent;
      final mock = MockClient((req) async {
        sent = jsonDecode(req.body) as Map<String, dynamic>;
        expect(req.method, 'PATCH');
        return http.Response(jsonEncode(scheduleJson()), 200);
      });
      await withClient(mock, () => svc().update('s1', title: 'New title', enabled: false));
      expect(sent, {'title': 'New title', 'enabled': false});
    });

    test('non-422 failures are logged with status and body and rethrown', () async {
      final mock = MockClient((_) async => http.Response(jsonEncode({'detail': 'boom'}), 500));
      Object? thrown;
      final logs = await captureLogs(() async {
        await withClient(mock, () async {
          try {
            await svc().approve('s1');
          } catch (e) {
            thrown = e;
          }
        });
      });
      expect(thrown, isA<ApiException>());
      expect(logs.join('\n'), contains('[ScheduleService] POST /schedules/s1/consent failed: status=500'));
    });
  });
}
