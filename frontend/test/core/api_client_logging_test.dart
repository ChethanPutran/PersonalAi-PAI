import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/tasks/task_service.dart';

/// Captures everything sent through debugPrint while [body] runs.
Future<List<String>> captureLogs(Future<void> Function() body) async {
  final logs = <String>[];
  final original = debugPrint;
  debugPrint = (String? message, {int? wrapWidth}) {
    if (message != null) logs.add(message);
  };
  try {
    await body();
  } finally {
    debugPrint = original;
  }
  return logs;
}

void main() {
  final api = ApiClient(baseUrl: 'http://backend.test', apiPrefix: '/api/v1');

  test('a 500 is logged with status, backend message and stack — and still throws', () async {
    final mock = MockClient((_) async => http.Response(
          jsonEncode({'detail': 'Failed to list tasks: 3 validation errors for Task'}),
          500,
          headers: {'content-type': 'application/json'},
        ));
    Object? thrown;
    final logs = await captureLogs(() async {
      await http.runWithClient(() async {
        try {
          await api.get('/tasks');
        } catch (e) {
          thrown = e;
        }
      }, () => mock);
    });
    expect(thrown, isA<ApiException>());
    expect((thrown as ApiException).statusCode, 500);
    final text = logs.join('\n');
    expect(text, contains('[ApiClient] GET /api/v1/tasks -> 500'));
    expect(text, contains('FAILED'));
    expect(text, contains('response body:'));
    expect(text, contains('3 validation errors for Task'));
  });

  test('successful calls are logged briefly and never leak the auth token', () async {
    final authed = ApiClient(
      baseUrl: 'http://backend.test',
      apiPrefix: '/api/v1',
      auth: null,
    );
    final mock = MockClient((req) async {
      return http.Response(jsonEncode({'ok': true}), 200);
    });
    final logs = await captureLogs(() async {
      await http.runWithClient(() => authed.get('/ping'), () => mock);
    });
    expect(logs.single, matches(RegExp(r'^\[ApiClient\] GET /api/v1/ping -> 200 \(\d+ ms\)$')));
    expect(logs.join(), isNot(contains('Bearer')));
  });

  test('a network failure is logged as such', () async {
    final mock = MockClient((_) async => throw http.ClientException('connection refused'));
    final logs = await captureLogs(() async {
      await http.runWithClient(() async {
        await expectLater(api.get('/tasks'), throwsA(isA<http.ClientException>()));
      }, () => mock);
    });
    expect(logs.join('\n'), contains('NETWORK ERROR'));
    expect(logs.join('\n'), contains('connection refused'));
  });

  test('TaskService keeps good tasks, logs the unparsable one, and fails only if none parse', () async {
    Map<String, dynamic> task(String id) => {
          'id': id,
          'name': 'n',
          'type': 'one_shot',
          'status': 'completed',
          'created_at': '2026-11-23T10:00:00+00:00',
        };
    final mixed = MockClient((_) async => http.Response(
          jsonEncode({'tasks': [task('a'), 42, task('b')]}), 200));
    late List tasks;
    final logs = await captureLogs(() async {
      await http.runWithClient(() async {
        tasks = await TaskService(api).getTasks();
      }, () => mixed);
    });
    expect(tasks.length, 2);
    expect(logs.join('\n'), contains('[TaskService] skipping unparsable task #1'));
    expect(logs.join('\n'), contains('parsed 2/3 tasks'));

    final allBad = MockClient((_) async => http.Response(jsonEncode({'tasks': [1, 2]}), 200));
    await captureLogs(() async {
      await http.runWithClient(() async {
        await expectLater(TaskService(api).getTasks(), throwsA(isA<StateError>()));
      }, () => allBad);
    });
  });
}
