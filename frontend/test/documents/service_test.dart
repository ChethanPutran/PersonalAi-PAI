import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/documents/document_index_service.dart';
import 'package:pai_mobile/documents/document_indexer.dart';

import 'indexer_test.dart' show FakeApi;

void main() {
  late Directory tmp;
  late FakeApi api;
  late DocumentIndexService svc;
  bool charging = false;
  late DateTime clock;
  late Map<String, dynamic> config;

  setUp(() {
    tmp = Directory.systemTemp.createTempSync('svc');
    final root = tmp.resolveSymbolicLinksSync();
    File('$root/docs/a.txt')
      ..createSync(recursive: true)
      ..writeAsStringSync('hello');
    api = FakeApi();
    charging = false;
    clock = DateTime(2026, 1, 1, 8);
    config = {'indexDocuments': true, 'allowedPaths': ['$root/docs']};
    final indexer = DocumentIndexer(
      api: api,
      store: IndexStateStore(File('$root/s.json')),
      deviceId: () => 'd',
      allFilesAccess: () async => true,
      appStorageRoots: () async => [],
      extractor: (p, n) async => ['x'],
    );
    svc = DocumentIndexService(
      indexer: indexer,
      api: api,
      deviceId: () => 'd',
      readConfig: () async => config,
      isCharging: () async => charging,
      allFilesAccess: () async => true,
      now: () => clock,
    );
  });
  tearDown(() => tmp.deleteSync(recursive: true));

  test('automatic runs index only while charging', () async {
    await svc.automatic(force: true);
    expect(api.server, isEmpty);
    charging = true;
    await svc.automatic(force: true);
    expect(api.server.length, 1);
  });

  test('Index now works on battery', () async {
    final r = await svc.indexNow();
    expect(r.filesSent, 1);
  });

  test('automatic runs are throttled unless forced', () async {
    charging = true;
    expect(await svc.automatic(), isNotNull);
    expect(await svc.automatic(), isNull);
    clock = clock.add(const Duration(minutes: 20));
    expect(await svc.automatic(), isNotNull);
  });

  test('settings turned off: cleans up at once even on battery', () async {
    charging = true;
    await svc.settingsChanged();
    expect(api.server.length, 1);
    charging = false;
    config = {...config, 'indexDocuments': false};
    await svc.settingsChanged();
    expect(api.server, isEmpty);
  });

  test('status combines server counts and local last run', () async {
    await svc.indexNow();
    final s = await svc.status();
    expect(s.server!.files, 1);
    expect(s.lastRun, isNotNull);
    expect(s.allFilesAccess, isTrue);
  });
}
