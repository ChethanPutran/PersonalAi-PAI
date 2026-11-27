import 'dart:io';

/// Dart port of the Kotlin `FileJail` boundary rules (files plugin): which files the user allowed.
///
/// Same rules as the plugin, so the indexer never reads anything the plugin itself would refuse:
///  * everything is compared on CANONICAL paths (symlinks resolved, `..` gone);
///  * prefix-sibling safe: `/a/b` does not contain `/a/bc`;
///  * a denied folder shuts a path out, unless an allowed root is MORE SPECIFIC (as long or longer than the
///    denied folder): denied `/Android/data` does not lock out allowed `/Android/data/<app>/files`, but
///    denied `/DCIM/private` does beat allowed `/DCIM`.
class PathJail {
  /// Canonical allowed roots / denied folders.
  final List<String> roots;
  final List<String> denied;
  final String Function(String) _canonical;

  PathJail._(this.roots, this.denied, this._canonical);

  factory PathJail({
    required Iterable<String> allowed,
    Iterable<String> denied = const [],
    String Function(String)? canonicalize,
  }) {
    final canon = canonicalize ?? canonicalPath;
    List<String> clean(Iterable<String> xs) {
      final out = <String>[];
      for (final x in xs) {
        final t = x.trim();
        if (t.isEmpty || !t.startsWith('/') || t.contains('\u0000')) continue;
        final c = canon(t);
        if (!out.contains(c)) out.add(c);
      }
      return out;
    }

    return PathJail._(clean(allowed), clean(denied), canon);
  }

  /// Prefix-sibling safe containment on canonical paths.
  static bool contains(String parent, String f) {
    if (f == parent) return true;
    final p = parent.endsWith('/') ? parent.substring(0, parent.length - 1) : parent;
    return f.startsWith('$p/');
  }

  /// True when [canonicalPath] is within an allowed root and not shut out by a denied folder.
  bool insideCanonical(String canonicalPath) {
    String? root;
    for (final r in roots) {
      if (contains(r, canonicalPath) && (root == null || r.length > root.length)) root = r;
    }
    if (root == null) return false;
    return !denied.any((d) => contains(d, canonicalPath) && d.length >= root!.length);
  }

  /// Canonicalizes [path] first (resolves symlinks), then checks it.
  bool allows(String path) {
    if (path.contains('\u0000') || !path.startsWith('/')) return false;
    return insideCanonical(_canonical(path));
  }

  /// A denied folder sits somewhere below [dir] and is shut out: [dir] as a whole reaches into it.
  bool hasDeniedInside(String dir) =>
      denied.any((d) => d != dir && contains(dir, d) && !insideCanonical(d));

  /// Canonical form: symlinks resolved as far as the path exists, the (non-existing) rest normalized.
  static String canonicalPath(String path) {
    final parts = <String>[];
    for (final seg in path.split('/')) {
      if (seg.isEmpty || seg == '.') continue;
      if (seg == '..') {
        if (parts.isNotEmpty) parts.removeLast();
        continue;
      }
      parts.add(seg);
    }
    var existing = '/${parts.join('/')}';
    final rest = <String>[];
    while (true) {
      try {
        final t = FileSystemEntity.typeSync(existing, followLinks: false);
        if (t != FileSystemEntityType.notFound) {
          final resolved = File(existing).resolveSymbolicLinksSync();
          return rest.isEmpty ? resolved : '${resolved == '/' ? '' : resolved}/${rest.reversed.join('/')}';
        }
      } on FileSystemException {
        // fall through: treat as not existing
      }
      final i = existing.lastIndexOf('/');
      if (i <= 0) {
        return rest.isEmpty ? '/' : '/${rest.reversed.join('/')}';
      }
      rest.add(existing.substring(i + 1));
      existing = existing.substring(0, i);
    }
  }
}
