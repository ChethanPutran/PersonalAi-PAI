import 'dart:async';

import 'package:flutter/foundation.dart';

import 'document_api.dart';
import 'document_indexer.dart';
import 'extractors.dart';

/// What the Document search section shows.
class DocumentIndexStatus {
  final IndexServerStatus? server;          // null: could not reach the server
  final DateTime? lastRun;
  final String lastMessage;
  final bool lastOk;
  final bool? allFilesAccess;
  final bool running;
  const DocumentIndexStatus({
    this.server,
    this.lastRun,
    this.lastMessage = '',
    this.lastOk = true,
    this.allFilesAccess,
    this.running = false,
  });
}

/// The seam the UI talks to (a fake in widget tests).
abstract class DocumentIndexController {
  Future<DocumentIndexStatus> status();
  Future<IndexRunResult> indexNow();
  Future<void> clear();
}

/// The id of the Android Files plugin whose settings drive the indexer.
const String kFilesAndroidPluginId = 'files_android';

/// When the indexer runs on the phone (documented rule, no foreground service):
///  * while the app is open: at connect, when the files settings change, and every [interval];
///  * automatic runs index new/changed files ONLY while the phone is charging; cleaning up (removing what was
///    deleted, denied or switched off) always runs because it is cheap;
///  * "Index now" runs at once, on battery too, and keeps going until done.
/// Only one run at a time; each file is read in a short-lived isolate.
class DocumentIndexService implements DocumentIndexController {
  final DocumentIndexer indexer;
  final DocumentIndexApi api;
  final String? Function() deviceId;

  /// The effective settings of the files plugin as stored on this phone.
  final Future<Map<String, dynamic>?> Function() readConfig;
  final Future<bool?> Function() isCharging;
  final Future<bool?> Function() allFilesAccess;
  final Duration interval;
  final Duration minGap;
  final DateTime Function() now;

  Timer? _timer;
  Future<IndexRunResult>? _running;
  DateTime? _lastAuto;

  DocumentIndexService({
    required this.indexer,
    required this.api,
    required this.deviceId,
    required this.readConfig,
    required this.isCharging,
    required this.allFilesAccess,
    this.interval = const Duration(hours: 6),
    this.minGap = const Duration(minutes: 15),
    DateTime Function()? now,
  }) : now = now ?? DateTime.now;

  /// Starts the periodic check (while the app is open).
  void start() {
    _timer?.cancel();
    _timer = Timer.periodic(interval, (_) => unawaited(automatic(force: true)));
  }

  void dispose() {
    _timer?.cancel();
    _timer = null;
  }

  Future<IndexSettings> _settings() async => IndexSettings.fromConfig(await readConfig());

  /// At app start / after connect / periodic. Throttled unless [force].
  Future<IndexRunResult?> automatic({bool force = false}) async {
    final t = now();
    if (!force && _lastAuto != null && t.difference(_lastAuto!) < minGap) return null;
    _lastAuto = t;
    return _run(indexNew: await isCharging() == true);
  }

  /// The plugin settings changed (turned off, folder removed, limit changed...): clean up at once, index
  /// new files when charging.
  Future<IndexRunResult> settingsChanged() async {
    _lastAuto = now();
    return _run(indexNew: await isCharging() == true);
  }

  @override
  Future<IndexRunResult> indexNow() => _run(indexNew: true);

  Future<IndexRunResult> _run({required bool indexNew}) {
    final running = _running;
    if (running != null) return running;
    final f = () async {
      try {
        final s = await _settings();
        return await indexer.run(s, indexNew: indexNew);
      } catch (e) {
        debugPrint('[DocumentIndexService] run failed: $e');
        return IndexRunResult(IndexOutcome.failed, message: 'Indexing stopped: $e');
      } finally {
        _running = null;
      }
    }();
    _running = f;
    return f;
  }

  @override
  Future<void> clear() async {
    await _running?.catchError((_) => const IndexRunResult(IndexOutcome.failed));
    await indexer.clear();
  }

  @override
  Future<DocumentIndexStatus> status() async {
    final state = await indexer.store.load();
    IndexServerStatus? server;
    final d = deviceId();
    if (d != null && d.isNotEmpty) {
      try {
        server = await api.status(d);
      } catch (_) {
        server = null;
      }
    }
    return DocumentIndexStatus(
      server: server,
      lastRun: state.lastRun,
      lastMessage: state.lastMessage,
      lastOk: state.lastOk,
      allFilesAccess: await allFilesAccess(),
      running: _running != null,
    );
  }
}

/// Where the app (which builds the service after login) puts it so settings screens can reach it.
class DocumentIndexHub {
  static DocumentIndexService? instance;
}

/// Types the phone can read, for the section's text.
String supportedTypesText() => kDocumentExtensions.map((e) => '.$e').join(' ');
