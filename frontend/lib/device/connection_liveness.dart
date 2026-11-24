/// Tracks whether the device WebSocket is still alive.
///
/// ANY frame from the server proves the socket works (heartbeat acks, pings, commands, task
/// events...), so every received frame calls [touch]. The watchdog declares the socket dead only
/// when nothing at all has arrived for [timeout]. Counting only `heartbeat_ack` frames, and never
/// refreshing the timestamp, made a healthy socket look dead after ~90s on every connection.
class ConnectionLiveness {
  ConnectionLiveness({this.timeout = const Duration(seconds: 90), DateTime Function()? now})
      : _now = now ?? DateTime.now;

  final Duration timeout;
  final DateTime Function() _now;
  DateTime? _last;

  /// Call when a connection opens and on every frame received.
  void touch() => _last = _now();

  /// Before the first [touch] there is nothing to judge, so it is never stale.
  Duration? get silence => _last == null ? null : _now().difference(_last!);

  bool get isStale {
    final s = silence;
    return s != null && s > timeout;
  }
}
