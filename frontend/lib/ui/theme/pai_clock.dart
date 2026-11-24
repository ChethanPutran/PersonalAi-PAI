/// The UI's notion of "now". Production uses the real clock; golden tests pin it (assign
/// [now]) so that "5 min ago" and message timestamps render identically on every run.
abstract final class PaiClock {
  static DateTime Function() now = DateTime.now;
}
