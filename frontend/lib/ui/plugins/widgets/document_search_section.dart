import 'package:flutter/material.dart';

import '../../../documents/document_index_service.dart';
import '../../../documents/document_indexer.dart';
import '../../../documents/extractors.dart';
import '../../theme/theme.dart';
import '../../widgets/hud/hud.dart';

/// "Document search" in the Android Files plugin settings: what is indexed, when it last ran, whether PAI can
/// reach the folders, and the "Index now" / "Clear index" buttons. Only for THIS device (the phone reads its
/// own files). The index itself lives on the PAI server; only extracted text chunks are sent there.
class DocumentSearchSection extends StatefulWidget {
  final DocumentIndexController controller;
  final DateTime Function()? now;

  const DocumentSearchSection({super.key, required this.controller, this.now});

  @override
  State<DocumentSearchSection> createState() => _DocumentSearchSectionState();
}

class _DocumentSearchSectionState extends State<DocumentSearchSection> with WidgetsBindingObserver {
  DocumentIndexStatus? _status;
  bool _loading = true;
  bool _busy = false;
  String? _message;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refresh();       // "All files access" may have changed in Settings
  }

  Future<void> _refresh() async {
    try {
      final s = await widget.controller.status();
      if (!mounted) return;
      setState(() {
        _status = s;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  Future<void> _indexNow() async {
    setState(() {
      _busy = true;
      _message = 'Indexing… this can take a while. You can leave this screen.';
    });
    final r = await widget.controller.indexNow();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _message = _resultText(r);
    });
    await _refresh();
  }

  String _resultText(IndexRunResult r) {
    switch (r.outcome) {
      case IndexOutcome.done:
        final parts = <String>[
          '${r.filesSent} file${r.filesSent == 1 ? '' : 's'} indexed',
          if (r.filesRemoved > 0) '${r.filesRemoved} removed',
          if (r.skippedTooBig > 0) '${r.skippedTooBig} too big',
          if (r.message.isNotEmpty) r.message,
        ];
        return '${parts.join(', ')}.';
      case IndexOutcome.off:
        return 'Document search is off.';
      default:
        return r.message.isEmpty ? "Couldn't index right now." : r.message;
    }
  }

  Future<void> _clear() async {
    final go = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Clear the document index?'),
        content: const Text(
            'PAI forgets the text it read from the files on this phone. Your files are not touched. '
            'You can index again any time.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(key: const Key('doc-clear-confirm'), onPressed: () => Navigator.pop(ctx, true), child: const Text('Clear index')),
        ],
      ),
    );
    if (go != true || !mounted) return;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await widget.controller.clear();
      if (!mounted) return;
      setState(() => _message = 'Index cleared.');
    } catch (_) {
      if (!mounted) return;
      setState(() => _message = "Couldn't reach the server to clear the index. It will be cleared when the phone is connected.");
    }
    if (!mounted) return;
    setState(() => _busy = false);
    await _refresh();
  }

  static String ago(DateTime then, DateTime now) {
    final d = now.difference(then);
    if (d.inMinutes < 1) return 'just now';
    if (d.inMinutes < 60) return '${d.inMinutes} min ago';
    if (d.inHours < 24) return '${d.inHours} h ago';
    return '${d.inDays} d ago';
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final s = _status;
    final enabled = s?.server?.indexingEnabled;
    final lines = <String>[];
    if (_loading) {
      lines.add('Loading…');
    } else if (s == null || s.server == null) {
      lines.add("Couldn't reach the server for the index status.");
    } else if (enabled == false) {
      lines.add("Off. Turn on \"Index documents\" above and save to let PAI search your files.");
    } else {
      final f = s.server!.files, c = s.server!.chunks;
      lines.add('$f file${f == 1 ? '' : 's'} indexed ($c text chunks).');
    }
    if (s?.lastRun != null) {
      lines.add('Last run ${ago(s!.lastRun!, (widget.now ?? DateTime.now)())}${s.lastMessage.isEmpty ? '' : ': ${s.lastMessage}'}');
    }
    final access = s?.allFilesAccess;
    if (access != null) {
      lines.add(access
          ? 'All files access is on: shared folders can be read.'
          : 'All files access is off: only PAI\'s own folder is read. Turn it on above to include Pictures, Download and so on.');
    }
    final canIndex = !_busy && enabled != false;
    return Container(
      key: const Key('document-search-section'),
      padding: const EdgeInsets.all(PaiSpacing.md),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest.withValues(alpha: 0.4),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            const Icon(Icons.manage_search, size: 20),
            const SizedBox(width: PaiSpacing.sm),
            Expanded(child: Text('Document search', style: text.titleSmall)),
            IconButton(
              key: const Key('doc-refresh'),
              icon: const Icon(Icons.refresh, size: 20),
              tooltip: 'Refresh status',
              onPressed: _busy ? null : _refresh,
            ),
          ]),
          const SizedBox(height: 4),
          Semantics(
            liveRegion: true,
            child: Text(lines.join('\n'), key: const Key('doc-status'), style: text.bodyMedium),
          ),
          const SizedBox(height: PaiSpacing.sm),
          Text(
            'Reads ${supportedTypesText()} files. $kPdfSupportNote. '
            'New and changed files are indexed automatically while the phone is charging; "Index now" works any time.',
            key: const Key('doc-note'),
            style: text.bodySmall,
          ),
          if (_message != null) ...[
            const SizedBox(height: PaiSpacing.sm),
            Text(_message!, key: const Key('doc-message'), style: text.bodyMedium?.copyWith(color: scheme.primary)),
          ],
          const SizedBox(height: PaiSpacing.md),
          Wrap(spacing: PaiSpacing.sm, runSpacing: PaiSpacing.sm, children: [
            GlowButton(
              key: const Key('doc-index-now'),
              label: _busy ? 'Working…' : 'Index now',
              icon: Icons.sync,
              loading: _busy,
              onPressed: canIndex ? _indexNow : null,
            ),
            GlowButton(
              key: const Key('doc-clear'),
              label: 'Clear index',
              icon: Icons.delete_outline,
              variant: GlowVariant.danger,
              onPressed: _busy ? null : _clear,
            ),
          ]),
        ],
      ),
    );
  }
}
