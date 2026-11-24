/// Decides whether a plugin reconcile should actually run.
///
/// The app reconciles with the backend whenever the WebSocket (re)connects. A flapping or
/// repeatedly reconnecting socket must not turn that into a stream of reconcile + catalog +
/// update requests. The gate lets the first one through, then skips further ones while one is
/// in flight or while the last successful one is younger than [minInterval]. A failed run does
/// not count, so the next connect retries. [force] bypasses the interval (never an in-flight run).
class ReconcileGate {
  ReconcileGate({this.minInterval = const Duration(seconds: 60), DateTime Function()? now})
      : _now = now ?? DateTime.now;

  final Duration minInterval;
  final DateTime Function() _now;
  bool _running = false;
  DateTime? _lastSuccess;

  bool get isRunning => _running;

  /// Runs [action] unless skipped. [action] returns true on success. Returns whether it ran.
  Future<bool> run(Future<bool> Function() action, {bool force = false}) async {
    if (_running) return false;
    final last = _lastSuccess;
    if (!force && last != null && _now().difference(last) < minInterval) return false;
    _running = true;
    try {
      if (await action()) _lastSuccess = _now();
      return true;
    } finally {
      _running = false;
    }
  }
}
