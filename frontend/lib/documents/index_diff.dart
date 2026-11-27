/// What a file looked like when it was indexed (the server keeps the same pair).
class FileMeta {
  final double mtime;
  final int size;
  const FileMeta(this.mtime, this.size);

  bool sameAs(FileMeta o) => size == o.size && (mtime - o.mtime).abs() < 0.001;

  Map<String, dynamic> toJson() => {'mtime': mtime, 'size': size};

  static FileMeta? fromJson(Object? j) {
    if (j is! Map) return null;
    final m = j['mtime'], s = j['size'];
    if (m is! num || s is! num) return null;
    return FileMeta(m.toDouble(), s.toInt());
  }
}

/// The work an index run has to do.
class SyncPlan {
  /// Eligible files that are new or changed (the server does not have them as they are now).
  final List<String> toSend;

  /// Paths the server holds that are no longer eligible (deleted, now denied, folder removed...).
  final List<String> toRemove;

  const SyncPlan(this.toSend, this.toRemove);
}

/// Incremental sync. [current]: every eligible file now (path -> meta). [server]: what the backend holds
/// (`GET /documents/files`). [empties]: files already read that had no text (the server stores nothing for
/// them, so only the local state remembers them), by the meta they had.
///
/// Removals are planned only when [walkComplete]: an interrupted folder walk must never look like "the user
/// deleted everything".
SyncPlan planSync({
  required Map<String, FileMeta> current,
  required Map<String, FileMeta> server,
  Map<String, FileMeta> empties = const {},
  bool walkComplete = true,
}) {
  final send = <String>[];
  for (final e in current.entries) {
    final onServer = server[e.key];
    if (onServer != null && onServer.sameAs(e.value)) continue;
    final empty = empties[e.key];
    if (empty != null && empty.sameAs(e.value)) continue;
    send.add(e.key);
  }
  final remove = walkComplete ? server.keys.where((p) => !current.containsKey(p)).toList() : <String>[];
  send.sort();
  remove.sort();
  return SyncPlan(send, remove);
}
