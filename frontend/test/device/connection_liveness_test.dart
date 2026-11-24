import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/device/connection_liveness.dart';

void main() {
  late DateTime t;
  late ConnectionLiveness live;
  setUp(() {
    t = DateTime(2026, 1, 1);
    live = ConnectionLiveness(now: () => t);
  });

  test('never stale before the first touch', () {
    t = t.add(const Duration(hours: 1));
    expect(live.isStale, isFalse);
    expect(live.silence, isNull);
  });

  test('stale only after the timeout passes with no frames', () {
    live.touch();
    t = t.add(const Duration(seconds: 90));
    expect(live.isStale, isFalse);                       // exactly at the limit: still fine
    t = t.add(const Duration(seconds: 1));
    expect(live.isStale, isTrue);
  });

  test('regression: a healthy socket (frames keep arriving) never goes stale', () {
    live.touch();                                        // connect
    // 10 minutes of traffic every 30s (heartbeat acks): the old code declared death at ~90s.
    for (var i = 0; i < 20; i++) {
      t = t.add(const Duration(seconds: 30));
      expect(live.isStale, isFalse, reason: 'after ${(i + 1) * 30}s');
      live.touch();
    }
  });

  test('a silent socket is caught even after a long healthy period', () {
    live.touch();
    t = t.add(const Duration(minutes: 30));
    live.touch();
    t = t.add(const Duration(seconds: 120));             // then nothing
    expect(live.isStale, isTrue);
    expect(live.silence, const Duration(seconds: 120));
  });
}
