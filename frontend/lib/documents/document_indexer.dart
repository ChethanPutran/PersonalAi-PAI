import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'document_api.dart';
import 'extractors.dart';
import 'index_diff.dart';
import 'path_jail.dart';

/// The Files plugin settings that matter to the indexer (the `files_android` effective settings).
class IndexSettings {
  final bool enabled;
  final List<String> allowed;
  final List<String> denied;
  final int maxFileMb;
  const IndexSettings({this.enabled = false, this.allowed = const [], this.denied = const [], this.maxFileMb = 20});

  factory IndexSettings.fromConfig(Map<String, dynamic>? c) {
    List<String> list(Object? v) => v is List ? [for (final e in v) e.toString()] : const [];
    final mb = c?['indexMaxFileMb'];
    return IndexSettings(
      enabled: c?['indexDocuments'] == true,
      allowed: list(c?['allowedPaths']),
      denied: list(c?['deniedPaths']),
      maxFileMb: mb is num && mb > 0 ? mb.toInt() : 20,
    );
  }
}

enum IndexOutcome { done, off, noFolders, indexingOffOnServer, offline, failed }

class IndexRunResult {
  final IndexOutcome outcome;
  final int filesSent;
  final int chunksSent;
  final int filesRemoved;
  final int skippedTooBig;
  final int skippedNoText;

  /// Allowed folders that were not read because "All files access" is off (and they are not app storage).
  final List<String> foldersNeedingAccess;

  /// The folder walk stopped early (limits), so nothing was removed this time.
  final bool incomplete;
  final String message;

  const IndexRunResult(
    this.outcome, {
    this.filesSent = 0,
    this.chunksSent = 0,
    this.filesRemoved = 0,
    this.skippedTooBig = 0,
    this.skippedNoText = 0,
    this.foldersNeedingAccess = const [],
    this.incomplete = false,
    this.message = '',
  });

  bool get ok => outcome == IndexOutcome.done;
}

/// Local bookkeeping in the app's documents directory (never contains file text).
class IndexState {
  /// Files already read that had no text, by the meta they had (the server stores nothing for them).
  Map<String, FileMeta> empties;
  DateTime? lastRun;
  String lastMessage;
  bool lastOk;

  /// A "delete everything" that could not reach the server yet: retried on the next run.
  bool pendingClear;
  bool wasEnabled;

  IndexState({
    this.empties = const {},
    this.lastRun,
    this.lastMessage = '',
    this.lastOk = true,
    this.pendingClear = false,
    this.wasEnabled = false,
  });

  Map<String, dynamic> toJson() => {
        'empties': {for (final e in empties.entries) e.key: e.value.toJson()},
        'last_run': lastRun?.toIso8601String(),
        'last_message': lastMessage,
        'last_ok': lastOk,
        'pending_clear': pendingClear,
        'was_enabled': wasEnabled,
      };

  factory IndexState.fromJson(Object? j) {
    if (j is! Map) return IndexState();
    final empties = <String, FileMeta>{};
    if (j['empties'] is Map) {
      for (final e in (j['empties'] as Map).entries) {
        final m = FileMeta.fromJson(e.value);
        if (m != null) empties[e.key.toString()] = m;
      }
    }
    return IndexState(
      empties: empties,
      lastRun: DateTime.tryParse(j['last_run']?.toString() ?? ''),
      lastMessage: j['last_message']?.toString() ?? '',
      lastOk: j['last_ok'] != false,
      pendingClear: j['pending_clear'] == true,
      wasEnabled: j['was_enabled'] == true,
    );
  }
}

class IndexStateStore {
  final File file;
  IndexStateStore(this.file);

  Future<IndexState> load() async {
    try {
      if (!await file.exists()) return IndexState();
      return IndexState.fromJson(jsonDecode(await file.readAsString()));
    } catch (_) {
      return IndexState();
    }
  }

  Future<void> save(IndexState s) async {
    try {
      await file.parent.create(recursive: true);
      await file.writeAsString(jsonEncode(s.toJson()));
    } catch (_) {/* bookkeeping only: the server stays the source of truth */}
  }

  Future<void> delete() async {
    try {
      if (await file.exists()) await file.delete();
    } catch (_) {}
  }
}

/// A file the walk found: canonical path plus what it looked like.
class FoundFile {
  final String path;
  final FileMeta meta;
  const FoundFile(this.path, this.meta);
}

class WalkResult {
  final Map<String, FileMeta> files;
  final int skippedTooBig;
  final bool complete;
  const WalkResult(this.files, this.skippedTooBig, this.complete);
}

const _skipDirNames = {'cache', 'thumbnails', 'node_modules', 'lost+found'};

/// Finds the indexable files under [roots] (already canonical). Same boundary as the Files plugin: only
/// paths [jail] allows; hidden folders/files, `.thumbnails`, cache folders and symlinked folders are skipped;
/// a symlinked file counts only when its target is inside the boundary. Bounded by [maxFiles]/[deadline].
Future<WalkResult> walkAllowed(
  PathJail jail,
  Iterable<String> roots, {
  required int maxFileBytes,
  int maxFiles = 50000,
  Duration budget = const Duration(minutes: 3),
  Set<String> extensions = kDocumentExtensions,
}) async {
  final found = <String, FileMeta>{};
  var tooBig = 0;
  var complete = true;
  final deadline = DateTime.now().add(budget);
  final seenDirs = <String>{};

  Future<void> walk(String dir) async {
    if (!seenDirs.add(dir)) return;
    if (!complete) return;
    Stream<FileSystemEntity> stream;
    try {
      stream = Directory(dir).list(followLinks: false);
    } catch (_) {
      return;
    }
    final subdirs = <String>[];
    try {
      await for (final e in stream) {
        if (DateTime.now().isAfter(deadline) || found.length >= maxFiles) {
          complete = false;
          return;
        }
        final name = e.path.substring(e.path.lastIndexOf('/') + 1);
        if (name.startsWith('.')) continue;
        final path = '$dir/$name';
        final type = FileSystemEntity.typeSync(path, followLinks: false);
        if (type == FileSystemEntityType.directory) {
          if (_skipDirNames.contains(name.toLowerCase())) continue;
          if (jail.insideCanonical(path)) subdirs.add(path);
        } else if (type == FileSystemEntityType.file || type == FileSystemEntityType.link) {
          if (!extensions.contains(extensionOf(name))) continue;
          var real = path;
          if (type == FileSystemEntityType.link) {
            real = PathJail.canonicalPath(path);           // a link out of the boundary is refused
            if (FileSystemEntity.typeSync(real, followLinks: false) != FileSystemEntityType.file) continue;
          }
          if (!jail.insideCanonical(real)) continue;
          final st = await File(real).stat();
          if (st.size <= 0) continue;
          if (st.size > maxFileBytes) {
            tooBig++;
            continue;
          }
          found[real] = FileMeta(st.modified.millisecondsSinceEpoch / 1000.0, st.size);
        }
      }
    } on FileSystemException {
      // an unreadable folder is skipped, the rest still counts
    }
    subdirs.sort();
    for (final d in subdirs) {
      await walk(d);
    }
  }

  for (final r in roots) {
    if (jail.insideCanonical(r)) await walk(r);
  }
  return WalkResult(found, tooBig, complete);
}

/// Extracts and chunks one file. Injectable so tests need no isolates.
typedef ChunkExtractor = Future<List<String>?> Function(String path, int maxChunks);

/// Incremental document indexer for the phone: finds the files the user allowed, reads their text on the
/// device (nothing is embedded here: the backend does that), sends only what changed and removes what is gone.
class DocumentIndexer {
  /// Chunks per request (server maximum is 500) and per file: a file's chunks must travel together.
  static const batchChunks = 400;
  static const maxChunksPerFile = 400;

  final DocumentIndexApi api;
  final IndexStateStore store;
  final String? Function() deviceId;

  /// Is "All files access" on? (null = unknown, treated as off).
  final Future<bool?> Function() allFilesAccess;

  /// App-private storage roots, readable without "All files access".
  final Future<List<String>> Function() appStorageRoots;
  final ChunkExtractor extractor;
  final Future<void> Function(Duration) sleep;
  final String Function(String) canonicalize;

  DocumentIndexer({
    required this.api,
    required this.store,
    required this.deviceId,
    required this.allFilesAccess,
    required this.appStorageRoots,
    ChunkExtractor? extractor,
    Future<void> Function(Duration)? sleep,
    String Function(String)? canonicalize,
  })  : extractor = extractor ?? ((p, n) => extractChunksInIsolate(p, maxChunks: n)),
        sleep = sleep ?? Future.delayed,
        canonicalize = canonicalize ?? PathJail.canonicalPath;

  /// Reconciles the server's index with the files now ([indexNew] false: only removes what is gone, which
  /// is cheap and needs no charger). [shouldStop] is polled between files (charger unplugged, app closing).
  Future<IndexRunResult> run(IndexSettings s, {bool indexNew = true, bool Function()? shouldStop}) async {
    final device = deviceId();
    if (device == null || device.isEmpty) {
      return const IndexRunResult(IndexOutcome.offline, message: 'Not connected to the PAI server yet.');
    }
    final state = await store.load();
    try {
      if (!s.enabled) return await _off(device, state);
      state.wasEnabled = true;
      if (state.pendingClear) {
        await api.remove(device);
        state.pendingClear = false;
        state.empties = {};
      }
      final jail = PathJail(allowed: s.allowed, denied: s.denied, canonicalize: canonicalize);
      if (jail.roots.isEmpty) {
        await _removeAll(device, state);
        return _finish(state, const IndexRunResult(IndexOutcome.noFolders,
            message: 'No folder is allowed yet. Add one in the Files settings.'));
      }

      final access = await allFilesAccess() == true;
      final appRoots = (await appStorageRoots()).map(canonicalize).toList();
      final readable = <String>[];
      final needAccess = <String>[];
      for (final r in jail.roots) {
        if (access || appRoots.any((a) => PathJail.contains(a, r))) {
          readable.add(r);
        } else {
          needAccess.add(r);
        }
      }

      final walk = await walkAllowed(jail, readable, maxFileBytes: s.maxFileMb * 1024 * 1024);
      final server = await api.indexedFiles(device);
      final plan = planSync(current: walk.files, server: server, empties: state.empties, walkComplete: walk.complete);

      var removed = 0;
      for (var i = 0; i < plan.toRemove.length; i += 100) {
        final part = plan.toRemove.sublist(i, i + 100 > plan.toRemove.length ? plan.toRemove.length : i + 100);
        await _retry(() => api.remove(device, paths: part));
        removed += part.length;
      }
      state.empties = {for (final e in state.empties.entries) if (walk.files.containsKey(e.key)) e.key: e.value};

      var filesSent = 0, chunksSent = 0, noText = 0;
      var stopped = false;
      if (indexNew) {
        var batch = <IndexFilePayload>[];
        var batchChunks = 0;
        Future<void> flush() async {
          if (batch.isEmpty) return;
          final b = batch;
          batch = [];
          batchChunks = 0;
          await _retry(() => api.index(device, b));
          filesSent += b.length;
        }

        for (final path in plan.toSend) {
          if (shouldStop?.call() == true) {
            stopped = true;
            break;
          }
          if (!jail.allows(path)) continue;                         // changed under us: re-check the boundary
          final meta = walk.files[path]!;
          final chunks = await extractor(path, maxChunksPerFile);
          if (chunks == null || chunks.isEmpty) {
            state.empties = {...state.empties, path: meta};
            noText++;
            continue;
          }
          if (batchChunks + chunks.length > DocumentIndexer.batchChunks && batch.isNotEmpty) await flush();
          batch.add(IndexFilePayload(path, meta.mtime, meta.size, chunks));
          batchChunks += chunks.length;
          chunksSent += chunks.length;
        }
        await flush();
      }

      final msg = needAccess.isNotEmpty
          ? 'Some folders were not read: turn on "All files access" for PAI in Android Settings.'
          : (stopped ? 'Paused (will continue later).' : '');
      return _finish(
        state,
        IndexRunResult(
          IndexOutcome.done,
          filesSent: filesSent,
          chunksSent: chunksSent,
          filesRemoved: removed,
          skippedTooBig: walk.skippedTooBig,
          skippedNoText: noText,
          foldersNeedingAccess: needAccess,
          incomplete: !walk.complete || stopped,
          message: msg,
        ),
      );
    } on IndexingOffException catch (e) {
      return _finish(state, IndexRunResult(IndexOutcome.indexingOffOnServer, message: e.message), ok: false);
    } on IndexTransientException catch (e) {
      return _finish(state, IndexRunResult(IndexOutcome.offline, message: e.message), ok: false);
    } on IndexLimitException catch (e) {
      return _finish(state, IndexRunResult(IndexOutcome.failed, message: e.message), ok: false);
    } catch (e) {
      return _finish(state, IndexRunResult(IndexOutcome.failed, message: "Indexing stopped: $e"), ok: false);
    }
  }


  /// Deletes everything this phone has in the index and the local bookkeeping.
  Future<void> clear() async {
    final device = deviceId();
    final state = await store.load();
    if (device == null || device.isEmpty) {
      state.pendingClear = true;
      state.empties = {};
      await store.save(state);
      return;
    }
    await _removeAll(device, state, rethrowOffline: true);
    state.lastRun = null;
    state.lastMessage = '';
    await store.save(state);
  }

  Future<IndexRunResult> _off(String device, IndexState state) async {
    if (state.wasEnabled || state.pendingClear || state.empties.isNotEmpty) {
      await _removeAll(device, state);
      state.wasEnabled = false;
    }
    return _finish(state, const IndexRunResult(IndexOutcome.off, message: 'Document search is off.'));
  }

  /// Server first; when it cannot be reached the wish is remembered (`pendingClear`) and retried next run.
  Future<void> _removeAll(String device, IndexState state, {bool rethrowOffline = false}) async {
    state.empties = {};
    try {
      await _retry(() => api.remove(device));
      state.pendingClear = false;
    } on IndexTransientException {
      state.pendingClear = true;
      if (rethrowOffline) {
        await store.save(state);
        rethrow;
      }
    }
  }

  Future<IndexRunResult> _finish(IndexState state, IndexRunResult r, {bool? ok}) async {
    state.lastRun = DateTime.now();
    state.lastOk = ok ?? true;
    state.lastMessage = r.message;
    await store.save(state);
    return r;
  }

  /// 3 attempts with growing waits on 429/5xx/offline; anything else is not retried.
  Future<T> _retry<T>(Future<T> Function() op) async {
    var wait = const Duration(seconds: 2);
    for (var attempt = 1;; attempt++) {
      try {
        return await op();
      } on IndexTransientException {
        if (attempt >= 3) rethrow;
        await sleep(wait);
        wait *= 2;
      }
    }
  }
}
