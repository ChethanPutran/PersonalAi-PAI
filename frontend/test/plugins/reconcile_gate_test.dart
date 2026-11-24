import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/plugins/reconcile_gate.dart';

void main() {
  late DateTime t;
  late ReconcileGate gate;
  late int calls;
  Future<bool> ok() async { calls++; return true; }
  Future<bool> fail() async { calls++; return false; }

  setUp(() {
    t = DateTime(2026, 1, 1);
    gate = ReconcileGate(now: () => t);
    calls = 0;
  });

  test('first run goes through', () async {
    expect(await gate.run(ok), isTrue);
    expect(calls, 1);
  });

  test('a reconnect storm runs only once', () async {
    for (var i = 0; i < 10; i++) {
      t = t.add(const Duration(seconds: 5));             // socket reconnects every 5s
      await gate.run(ok);
    }
    expect(calls, 1);
  });

  test('runs again once the interval has passed', () async {
    await gate.run(ok);
    t = t.add(const Duration(seconds: 61));
    expect(await gate.run(ok), isTrue);
    expect(calls, 2);
  });

  test('a failed run does not suppress the retry', () async {
    await gate.run(fail);
    t = t.add(const Duration(seconds: 5));
    expect(await gate.run(ok), isTrue);
    expect(calls, 2);
  });

  test('an exception does not wedge the gate', () async {
    await expectLater(gate.run(() async => throw StateError('boom')), throwsStateError);
    expect(gate.isRunning, isFalse);
    expect(await gate.run(ok), isTrue);
  });

  test('never two at once, even when forced', () async {
    final release = Completer<bool>();
    final first = gate.run(() { calls++; return release.future; });
    expect(await gate.run(ok, force: true), isFalse);
    expect(calls, 1);
    release.complete(true);
    await first;
  });

  test('force bypasses the interval', () async {
    await gate.run(ok);
    expect(await gate.run(ok, force: true), isTrue);
    expect(calls, 2);
  });
}
