import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/documents/document_api.dart';
import 'package:pai_mobile/documents/document_index_service.dart';
import 'package:pai_mobile/documents/document_indexer.dart';
import 'package:pai_mobile/ui/plugins/widgets/document_search_section.dart';

class FakeController implements DocumentIndexController {
  DocumentIndexStatus current;
  int indexed = 0, cleared = 0;
  IndexRunResult result = const IndexRunResult(IndexOutcome.done, filesSent: 3);
  Object? clearError;
  FakeController(this.current);

  @override
  Future<DocumentIndexStatus> status() async => current;
  @override
  Future<IndexRunResult> indexNow() async {
    indexed++;
    return result;
  }

  @override
  Future<void> clear() async {
    cleared++;
    if (clearError != null) throw clearError!;
  }
}

Future<void> pumpSection(WidgetTester t, FakeController c) async {
  await t.pumpWidget(MaterialApp(
    home: Scaffold(
      body: SingleChildScrollView(child: DocumentSearchSection(key: UniqueKey(), controller: c, now: () => DateTime(2026, 1, 1, 12))),
    ),
  ));
  await t.pump();
  await t.pump(const Duration(milliseconds: 100));
}

void main() {
  testWidgets('shows counts, last run, access state, PDF note', (t) async {
    final c = FakeController(DocumentIndexStatus(
      server: const IndexServerStatus(files: 12, chunks: 340, indexingEnabled: true),
      lastRun: DateTime(2026, 1, 1, 11, 30),
      lastMessage: '',
      allFilesAccess: false,
    ));
    await pumpSection(t, c);
    final status = (t.widget(find.byKey(const Key('doc-status'))) as Text).data!;
    expect(status, contains('12 files indexed (340 text chunks)'));
    expect(status, contains('Last run 30 min ago'));
    expect(status, contains('All files access is off'));
    expect((t.widget(find.byKey(const Key('doc-note'))) as Text).data, contains('PDF not supported on this phone yet'));
  });

  testWidgets('index now runs and reports; disabled while the setting is off', (t) async {
    final c = FakeController(const DocumentIndexStatus(server: IndexServerStatus(indexingEnabled: true)));
    await pumpSection(t, c);
    await t.tap(find.byKey(const Key('doc-index-now')));
    await t.pump();
    await t.pump(const Duration(milliseconds: 100));
    expect(c.indexed, 1);
    expect((t.widget(find.byKey(const Key('doc-message'))) as Text).data, contains('3 files indexed'));

    final off = FakeController(const DocumentIndexStatus(server: IndexServerStatus(indexingEnabled: false)));
    await pumpSection(t, off);
    expect((t.widget(find.byKey(const Key('doc-status'))) as Text).data, contains('Off.'));
    await t.tap(find.byKey(const Key('doc-index-now')), warnIfMissed: false);
    await t.pump();
    expect(off.indexed, 0);
  });

  testWidgets('clear index asks first; cancel does nothing, confirm clears', (t) async {
    final c = FakeController(const DocumentIndexStatus(server: IndexServerStatus(files: 1, chunks: 2, indexingEnabled: true)));
    await pumpSection(t, c);
    await t.tap(find.byKey(const Key('doc-clear')));
    await t.pump();
    await t.pump(const Duration(milliseconds: 300));
    await t.tap(find.text('Cancel'));
    await t.pump();
    await t.pump(const Duration(milliseconds: 300));
    expect(c.cleared, 0);

    await t.tap(find.byKey(const Key('doc-clear')));
    await t.pump();
    await t.pump(const Duration(milliseconds: 300));
    await t.tap(find.byKey(const Key('doc-clear-confirm')));
    await t.pump();
    await t.pump(const Duration(milliseconds: 300));
    expect(c.cleared, 1);
    expect((t.widget(find.byKey(const Key('doc-message'))) as Text).data, 'Index cleared.');
  });

  testWidgets('a clear that cannot reach the server says so', (t) async {
    final c = FakeController(const DocumentIndexStatus(server: IndexServerStatus(indexingEnabled: true)))
      ..clearError = const IndexTransientException('x');
    await pumpSection(t, c);
    await t.tap(find.byKey(const Key('doc-clear')));
    await t.pump();
    await t.pump(const Duration(milliseconds: 300));
    await t.tap(find.byKey(const Key('doc-clear-confirm')));
    await t.pump();
    await t.pump(const Duration(milliseconds: 300));
    expect((t.widget(find.byKey(const Key('doc-message'))) as Text).data, contains("Couldn't reach the server"));
  });

  testWidgets('server unreachable shows a plain line', (t) async {
    await pumpSection(t, FakeController(const DocumentIndexStatus()));
    expect((t.widget(find.byKey(const Key('doc-status'))) as Text).data, contains("Couldn't reach the server"));
  });
}
