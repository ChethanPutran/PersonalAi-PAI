import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/documents/document_api.dart';
import 'package:pai_mobile/documents/document_indexer.dart';
import 'package:pai_mobile/documents/index_diff.dart';

class FakeApi implements DocumentIndexApi {
  Map<String, FileMeta> server = {};
  final sent = <List<IndexFilePayload>>[];
  final removed = <List<String>>[];
  int removeAll = 0;
  Object? indexError;
  int transientFailures = 0;
  int indexCalls = 0;

  @override
  Future<Map<String, FileMeta>> indexedFiles(String d) async => Map.of(server);

  @override
  Future<void> index(String d, List<IndexFilePayload> files) async {
    indexCalls++;
    if (transientFailures > 0) {
      transientFailures--;
      throw const IndexTransientException('busy');
    }
    if (indexError != null) throw indexError!;
    sent.add(files);
    for (final f in files) {
      server[f.path] = FileMeta(f.mtime, f.size);
    }
  }

  @override
  Future<int> remove(String d, {List<String> paths = const [], String? prefix}) async {
    if (paths.isEmpty && prefix == null) {
      removeAll++;
      server.clear();
      return 0;
    }
    removed.add(paths);
    paths.forEach(server.remove);
    return paths.length;
  }

  @override
  Future<IndexServerStatus> status(String d) async => IndexServerStatus(files: server.length);
}

void main() {
  late Directory tmp;
  late String root;
  late FakeApi api;
  late DocumentIndexer indexer;
  bool access = true;
  List<String> appRoots = [];
  final sleeps = <Duration>[];

  setUp(() {
    tmp = Directory.systemTemp.createTempSync('idx');
    root = tmp.resolveSymbolicLinksSync();
    api = FakeApi();
    access = true;
    appRoots = [];
    sleeps.clear();
    indexer = DocumentIndexer(
      api: api,
      store: IndexStateStore(File('$root/state/s.json')),
      deviceId: () => 'dev1',
      allFilesAccess: () async => access,
      appStorageRoots: () async => appRoots,
      extractor: (p, n) async {
        final t = File(p).readAsStringSync();
        return t.trim().isEmpty ? null : [t];
      },
      sleep: (d) async => sleeps.add(d),
    );
  });
  tearDown(() => tmp.deleteSync(recursive: true));

  File put(String rel, String text) {
    final f = File('$root/$rel')..createSync(recursive: true);
    f.writeAsStringSync(text);
    return f;
  }

  IndexSettings settings({List<String>? allowed, List<String> denied = const [], bool on = true, int mb = 20}) =>
      IndexSettings(enabled: on, allowed: allowed ?? ['$root/docs'], denied: denied, maxFileMb: mb);

  test('indexes supported files, skips hidden/cache/thumbnails/unsupported/denied', () async {
    put('docs/a.txt', 'alpha');
    put('docs/sub/b.md', 'beta');
    put('docs/.hidden/c.txt', 'no');
    put('docs/.thumbnails/d.txt', 'no');
    put('docs/Cache/e.txt', 'no');
    put('docs/f.jpg', 'no');
    put('docs/f.pdf', 'no');
    put('docs/private/g.txt', 'no');
    final r = await indexer.run(settings(denied: ['$root/docs/private']));
    expect(r.ok, isTrue);
    expect(api.server.keys.toSet(), {'$root/docs/a.txt', '$root/docs/sub/b.md'});
    expect(r.filesSent, 2);
  });

  test('second run sends nothing; a changed file is re-sent; a deleted one is removed', () async {
    final a = put('docs/a.txt', 'alpha');
    put('docs/b.txt', 'beta');
    await indexer.run(settings());
    api.sent.clear();
    var r = await indexer.run(settings());
    expect(r.filesSent, 0);
    expect(api.sent, isEmpty);

    a.writeAsStringSync('alpha changed');
    File('$root/docs/b.txt').deleteSync();
    r = await indexer.run(settings());
    expect(r.filesSent, 1);
    expect(r.filesRemoved, 1);
    expect(api.removed.single, ['$root/docs/b.txt']);
    expect(api.server.keys, ['$root/docs/a.txt']);
  });

  test('a folder removed from the settings, or newly denied, is removed from the server', () async {
    put('docs/a.txt', 'a');
    put('more/b.txt', 'b');
    await indexer.run(settings(allowed: ['$root/docs', '$root/more']));
    expect(api.server.length, 2);
    await indexer.run(settings(allowed: ['$root/docs']));
    expect(api.server.keys, ['$root/docs/a.txt']);
    await indexer.run(settings(denied: ['$root/docs']));
    expect(api.server, isEmpty);
  });

  test('turning the setting off clears the server and the local state', () async {
    put('docs/a.txt', 'a');
    put('docs/empty.txt', '   ');
    await indexer.run(settings());
    var st = await indexer.store.load();
    expect(st.empties, isNotEmpty);
    final r = await indexer.run(settings(on: false));
    expect(r.outcome, IndexOutcome.off);
    expect(api.removeAll, 1);
    expect(api.server, isEmpty);
    st = await indexer.store.load();
    expect(st.empties, isEmpty);
    // off again: nothing more to do
    await indexer.run(settings(on: false));
    expect(api.removeAll, 1);
  });

  test('files too big are skipped and counted; empty-text files are not retried', () async {
    put('docs/big.txt', 'x' * (1024 * 1024 + 5));
    put('docs/blank.txt', '  ');
    put('docs/ok.txt', 'ok');
    final r = await indexer.run(settings(mb: 1));
    expect(r.skippedTooBig, 1);
    expect(r.skippedNoText, 1);
    expect(api.server.keys, ['$root/docs/ok.txt']);
    var calls = 0;
    final again = DocumentIndexer(
      api: api,
      store: indexer.store,
      deviceId: () => 'dev1',
      allFilesAccess: () async => true,
      appStorageRoots: () async => [],
      extractor: (p, n) async {
        calls++;
        return null;
      },
    );
    await again.run(settings(mb: 1));
    expect(calls, 0);
  });

  test('symlinks out of the boundary are refused, links inside are followed for files', () async {
    put('docs/in.txt', 'in');
    put('outside/secret.txt', 'secret');
    Link('$root/docs/escape').createSync('$root/outside');
    Link('$root/docs/leak.txt').createSync('$root/outside/secret.txt');
    Link('$root/docs/alias.txt').createSync('$root/docs/in.txt');
    await indexer.run(settings());
    expect(api.server.keys.toSet(), {'$root/docs/in.txt'});
  });

  test('without All files access, shared folders are reported, not read; app folders are', () async {
    put('shared/a.txt', 'a');
    put('app/files/b.txt', 'b');
    access = false;
    appRoots = ['$root/app'];
    final r = await indexer.run(settings(allowed: ['$root/shared', '$root/app/files']));
    expect(r.foldersNeedingAccess, ['$root/shared']);
    expect(r.message, contains('All files access'));
    expect(api.server.keys, ['$root/app/files/b.txt']);
  });

  test('batches stay within 400 chunks; a file travels whole', () async {
    for (var i = 0; i < 5; i++) {
      put('docs/f$i.txt', 'x');
    }
    final multi = DocumentIndexer(
      api: api,
      store: indexer.store,
      deviceId: () => 'dev1',
      allFilesAccess: () async => true,
      appStorageRoots: () async => [],
      extractor: (p, n) async => List.filled(150, 'chunk'),
    );
    final r = await multi.run(settings());
    expect(r.chunksSent, 750);
    expect(api.sent.every((b) => b.fold<int>(0, (s, f) => s + f.chunks.length) <= 400), isTrue);
    expect(api.sent.expand((b) => b).length, 5);
  });

  test('429/5xx are retried with backoff; persistent failure reports offline', () async {
    put('docs/a.txt', 'a');
    api.transientFailures = 2;
    var r = await indexer.run(settings());
    expect(r.ok, isTrue);
    expect(sleeps, [const Duration(seconds: 2), const Duration(seconds: 4)]);
    put('docs/b.txt', 'b');
    api.transientFailures = 99;
    r = await indexer.run(settings());
    expect(r.outcome, IndexOutcome.offline);
  });

  test('409 indexing_off stops with a clear message and is not retried', () async {
    put('docs/a.txt', 'a');
    api.indexError = const IndexingOffException();
    final r = await indexer.run(settings());
    expect(r.outcome, IndexOutcome.indexingOffOnServer);
    expect(r.message, contains('Index documents'));
    expect(api.indexCalls, 1);
  });

  test('indexNew false only cleans up', () async {
    final a = put('docs/a.txt', 'a');
    await indexer.run(settings());
    a.deleteSync();
    put('docs/new.txt', 'new');
    final r = await indexer.run(settings(), indexNew: false);
    expect(r.filesRemoved, 1);
    expect(r.filesSent, 0);
    expect(api.server, isEmpty);
  });

  test('clear removes everything on the server and local state', () async {
    put('docs/a.txt', 'a');
    await indexer.run(settings());
    await indexer.clear();
    expect(api.server, isEmpty);
    expect(api.removeAll, 1);
  });

  test('no folders allowed clears the index; no device id means not connected', () async {
    put('docs/a.txt', 'a');
    await indexer.run(settings());
    final r = await indexer.run(settings(allowed: const []));
    expect(r.outcome, IndexOutcome.noFolders);
    expect(api.server, isEmpty);
    final lonely = DocumentIndexer(
        api: api, store: indexer.store, deviceId: () => null, allFilesAccess: () async => true, appStorageRoots: () async => []);
    expect((await lonely.run(settings())).outcome, IndexOutcome.offline);
  });

  test('IndexSettings reads the plugin config with safe defaults', () {
    final s = IndexSettings.fromConfig({'indexDocuments': true, 'allowedPaths': ['/a'], 'indexMaxFileMb': 5});
    expect(s.enabled, isTrue);
    expect(s.allowed, ['/a']);
    expect(s.maxFileMb, 5);
    final d = IndexSettings.fromConfig(null);
    expect(d.enabled, isFalse);
    expect(d.maxFileMb, 20);
  });
}
