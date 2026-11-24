import 'dart:async';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
// `markdown` is flutter_markdown_plus's own dependency (needed for the builder signatures);
// it is intentionally not duplicated in pubspec.yaml.
// ignore: depend_on_referenced_packages
import 'package:markdown/markdown.dart' as md;
import 'package:url_launcher/url_launcher.dart';

import '../../theme/theme.dart';
import 'markdown_safety.dart';
import 'streaming_markdown_preprocessor.dart';

/// Opens a (already user-confirmed, already validated) link. Injectable for tests.
typedef LinkLauncher = Future<bool> Function(Uri uri);

/// Creates the provider for a remote image the user explicitly chose to load. Injectable so
/// tests can prove that nothing is fetched before the tap.
typedef RemoteImageProviderFactory = ImageProvider Function(Uri uri);

Future<bool> _defaultLauncher(Uri uri) => launchUrl(uri, mode: LaunchMode.externalApplication);
ImageProvider _defaultImageProvider(Uri uri) => NetworkImage(uri.toString());

/// Renders an ASSISTANT message as markdown — safely and stream-friendly.
///
/// * headings, emphasis, inline/fenced code (copy button), lists, task lists, quotes, rules,
///   tables (horizontal scroll), links, images — all selectable.
/// * **Streaming**: while [streaming] is true the text is "closed" for display only
///   ([StreamingMarkdownPreprocessor]) and rebuilds are coalesced to [throttle]; the stored
///   text is never modified and the exact final text is rendered once streaming ends.
/// * **Hostile-input safe**: no raw HTML; links only http/https/mailto and confirmed (with the
///   real URL) before opening; remote images are NOT fetched until tapped; size and nesting
///   are bounded ([MarkdownLimits]) with a "Show more" button for very long replies.
class MarkdownMessage extends StatefulWidget {
  const MarkdownMessage({
    super.key,
    required this.data,
    this.streaming = false,
    this.linkLauncher = _defaultLauncher,
    this.imageProviderFactory = _defaultImageProvider,
    this.limits = const MarkdownLimits(),
    this.throttle = const Duration(milliseconds: 50),
  });

  final String data;
  final bool streaming;
  final LinkLauncher linkLauncher;
  final RemoteImageProviderFactory imageProviderFactory;
  final MarkdownLimits limits;

  /// Minimum time between re-renders while [streaming] (0 = render every update).
  final Duration throttle;

  @override
  State<MarkdownMessage> createState() => _MarkdownMessageState();
}

class _MarkdownMessageState extends State<MarkdownMessage> {
  late String _shown = widget.data;
  Timer? _timer;
  bool _expanded = false;

  // Link gesture recognizers handed to the markdown tree; disposed with this state.
  final List<_LinkGesture> _linkGestures = [];

  // Memo of the last prepared text, so unrelated rebuilds (scroll, theme) don't re-parse.
  String? _memoInput;
  bool? _memoStreaming;
  bool? _memoExpanded;
  String _memoOutput = '';
  bool _memoTruncated = false;

  @override
  void didUpdateWidget(MarkdownMessage old) {
    super.didUpdateWidget(old);
    if (widget.data == old.data && widget.streaming == old.streaming) return;
    final stillStreaming = widget.streaming && old.streaming;
    if (!stillStreaming || widget.throttle == Duration.zero) {
      // Not mid-stream (or throttling disabled): show the latest text immediately — in
      // particular the exact final text the moment streaming ends.
      _timer?.cancel();
      _timer = null;
      _shown = widget.data;
      return;
    }
    // Mid-stream: coalesce bursts of tokens into one re-render per [throttle].
    _timer ??= Timer(widget.throttle, () {
      _timer = null;
      if (mounted) setState(() => _shown = widget.data);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    for (final g in _linkGestures) {
      g.dispose();
    }
    super.dispose();
  }

  // ---------------------------------------------------------------- preparation

  void _prepare() {
    if (_memoInput == _shown && _memoStreaming == widget.streaming && _memoExpanded == _expanded) {
      return;
    }
    final lim = widget.limits;
    var text = _shown;
    final truncated = text.length > lim.maxChars && !_expanded;
    if (truncated) text = truncateAtLineBoundary(text, lim.maxChars);
    if (widget.streaming || truncated) text = StreamingMarkdownPreprocessor.close(text);
    _memoOutput = boundMarkdown(escapeRawHtml(text), lim);
    _memoTruncated = truncated;
    _memoInput = _shown;
    _memoStreaming = widget.streaming;
    _memoExpanded = _expanded;
  }

  // ---------------------------------------------------------------- links

  _LinkGesture _newLinkGesture(Uri uri) {
    final g = _LinkGesture(
      uri: uri,
      onOpen: _confirmAndOpen,
      onPreview: _previewLink,
    );
    _linkGestures.add(g);
    return g;
  }

  void _previewLink(Uri uri) {
    ScaffoldMessenger.maybeOf(context)
      ?..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(uri.toString(), maxLines: 3, overflow: TextOverflow.ellipsis),
        duration: const Duration(seconds: 4),
      ));
  }

  Future<void> _confirmAndOpen(Uri uri) async {
    final choice = await showDialog<_LinkChoice>(
      context: context,
      builder: (_) => LinkConfirmDialog(uri: uri),
    );
    if (!mounted || choice == null || choice == _LinkChoice.cancel) return;
    if (choice == _LinkChoice.copy) {
      await Clipboard.setData(ClipboardData(text: uri.toString()));
      if (!mounted) return;
      ScaffoldMessenger.maybeOf(context)?.showSnackBar(const SnackBar(content: Text('Link copied')));
      return;
    }
    try {
      final ok = await widget.linkLauncher(uri);
      if (!ok) _toast('Could not open the link');
    } catch (e, st) {
      debugPrint('[MarkdownMessage] opening $uri failed: $e');
      debugPrintStack(stackTrace: st, label: '[MarkdownMessage] link', maxFrames: 6);
      _toast('Could not open the link');
    }
  }

  void _toast(String message) {
    if (!mounted) return;
    ScaffoldMessenger.maybeOf(context)?.showSnackBar(SnackBar(content: Text(message)));
  }

  // ---------------------------------------------------------------- build

  @override
  Widget build(BuildContext context) {
    _prepare();
    final theme = Theme.of(context);
    final pai = context.pai;

    final body = MarkdownBody(
      data: _memoOutput,
      selectable: false, // selection is provided by the SelectionArea below (spans blocks)
      softLineBreak: true,
      shrinkWrap: true,
      styleSheet: _styleSheet(theme, pai),
      builders: <String, MarkdownElementBuilder>{
        'pre': _CodeBlockBuilder(),
        'a': _LinkBuilder(this),
      },
      imageBuilder: (uri, title, alt) => RemoteImagePlaceholder(
        uri: uri,
        alt: alt ?? title,
        imageProviderFactory: widget.imageProviderFactory,
      ),
      checkboxBuilder: (checked) => Padding(
        padding: const EdgeInsets.only(right: 6),
        child: Icon(
          checked ? Icons.check_box : Icons.check_box_outline_blank,
          size: 18,
          color: checked ? pai.cyan : pai.faint,
          semanticLabel: checked ? 'done' : 'not done',
        ),
      ),
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        SelectionArea(child: body),
        if (_memoTruncated)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: () => setState(() => _expanded = true),
              icon: const Icon(Icons.unfold_more, size: 18),
              label: Text('Show more (${_shown.length - widget.limits.maxChars} more characters)'),
            ),
          ),
      ],
    );
  }

  MarkdownStyleSheet _styleSheet(ThemeData theme, PaiPalette pai) {
    final text = theme.textTheme;
    final base = text.bodyLarge?.copyWith(color: pai.strong, height: 1.45);
    TextStyle? heading(TextStyle? s) => s?.copyWith(color: pai.strong, height: 1.25);
    return MarkdownStyleSheet.fromTheme(theme).copyWith(
      p: base,
      pPadding: EdgeInsets.zero,
      a: base?.copyWith(color: pai.cyan, decoration: TextDecoration.underline, decorationColor: pai.cyan),
      h1: heading(text.titleLarge),
      h2: heading(text.titleMedium),
      h3: heading(text.titleSmall),
      h4: heading(text.labelLarge),
      h5: heading(text.labelLarge),
      h6: heading(text.labelMedium),
      strong: base?.copyWith(fontWeight: FontWeight.w700),
      em: base?.copyWith(fontStyle: FontStyle.italic),
      del: base?.copyWith(decoration: TextDecoration.lineThrough, color: pai.muted),
      code: pai.mono(size: 13, strong: true).copyWith(backgroundColor: pai.cyan.withValues(alpha: 0.12)),
      codeblockPadding: EdgeInsets.zero,
      codeblockDecoration: BoxDecoration(
        color: pai.codeBg,
        borderRadius: BorderRadius.circular(PaiRadii.sm),
        border: Border.all(color: pai.line),
      ),
      blockquote: base?.copyWith(color: pai.muted),
      blockquotePadding: const EdgeInsets.fromLTRB(12, 6, 8, 6),
      blockquoteDecoration: BoxDecoration(
        color: pai.cyan.withValues(alpha: 0.06),
        border: Border(left: BorderSide(color: pai.cyan.withValues(alpha: 0.6), width: 3)),
        borderRadius: const BorderRadius.horizontal(right: Radius.circular(4)),
      ),
      horizontalRuleDecoration: BoxDecoration(
        border: Border(top: BorderSide(color: pai.line, width: 1)),
      ),
      listBullet: base?.copyWith(color: pai.cyan),
      listIndent: 22,
      blockSpacing: 10,
      tableHead: base?.copyWith(fontWeight: FontWeight.w700),
      tableBody: base,
      tableBorder: TableBorder.all(color: pai.line, width: 1),
      tableColumnWidth: const IntrinsicColumnWidth(), // wide tables scroll horizontally
      tableCellsPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      tableHeadCellsPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      tableHeadCellsDecoration: BoxDecoration(color: pai.cyan.withValues(alpha: 0.10)),
    );
  }
}

// =======================================================================================
// Links
// =======================================================================================

enum _LinkChoice { open, copy, cancel }

/// Tap → confirmation dialog with the real URL. Long-press → shows the URL.
class _LinkGesture extends TapGestureRecognizer {
  _LinkGesture({required this.uri, required void Function(Uri) onOpen, required void Function(Uri) onPreview}) {
    onTapDown = (_) {
      _longPressed = false;
      _timer?.cancel();
      // Must fire BEFORE the system long-press (500 ms after touch-down) that SelectionArea
      // uses to start text selection, because that one wins the gesture arena and cancels this tap.
      _timer = Timer(const Duration(milliseconds: 300), () {
        _longPressed = true;
        onPreview(uri);
      });
    };
    onTapCancel = () => _timer?.cancel();
    onTapUp = (_) => _timer?.cancel();
    onTap = () {
      if (_longPressed) {
        _longPressed = false; // the long press already showed the URL
        return;
      }
      onOpen(uri);
    };
  }

  final Uri uri;
  Timer? _timer;
  bool _longPressed = false;

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }
}

class _LinkBuilder extends MarkdownElementBuilder {
  _LinkBuilder(this._state);
  final _MarkdownMessageState _state;

  @override
  bool isBlockElement() => false;

  @override
  Widget? visitElementAfterWithContext(
    BuildContext context,
    md.Element element,
    TextStyle? preferredStyle,
    TextStyle? parentStyle,
  ) {
    final label = element.textContent;
    final style = parentStyle?.merge(preferredStyle) ?? preferredStyle;
    final uri = parseSafeLink(element.attributes['href']);
    if (uri == null) {
      // javascript:, data:, file:, intent:, ... — shown as inert text, never tappable.
      return Text.rich(TextSpan(text: label, style: parentStyle));
    }
    return Text.rich(TextSpan(text: label, style: style, recognizer: _state._newLinkGesture(uri)));
  }
}

/// "Open this link?" — shows exactly where the link goes before anything is opened.
class LinkConfirmDialog extends StatelessWidget {
  const LinkConfirmDialog({super.key, required this.uri});
  final Uri uri;

  @override
  Widget build(BuildContext context) {
    final pai = context.pai;
    final isMail = uri.scheme == 'mailto';
    final primary = isMail ? uri.path : displayHost(uri);
    return AlertDialog(
      title: const Text('Open this link?'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(isMail ? 'Send an email to' : 'This opens', style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 4),
            Text(primary, style: pai.mono(size: 16, color: pai.cyan)),
            const SizedBox(height: 10),
            SelectableText(uri.toString(), style: pai.mono(size: 12)),
            if (hasUnusualHost(uri)) ...[
              const SizedBox(height: 10),
              Text(
                'This address contains unusual characters — it may imitate another site.',
                style: TextStyle(color: pai.amber),
              ),
            ],
            const SizedBox(height: 10),
            Text(
              'Links in AI replies can be wrong or unsafe. Only continue if you trust it.',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(color: pai.muted),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, _LinkChoice.cancel), child: const Text('Cancel')),
        TextButton(onPressed: () => Navigator.pop(context, _LinkChoice.copy), child: const Text('Copy link')),
        FilledButton(onPressed: () => Navigator.pop(context, _LinkChoice.open), child: const Text('Open')),
      ],
    );
  }
}

// =======================================================================================
// Code blocks
// =======================================================================================

class _CodeBlockBuilder extends MarkdownElementBuilder {
  @override
  Widget? visitElementAfterWithContext(
    BuildContext context,
    md.Element element,
    TextStyle? preferredStyle,
    TextStyle? parentStyle,
  ) {
    String language = '';
    final first = (element.children?.isNotEmpty ?? false) ? element.children!.first : null;
    if (first is md.Element) {
      final cls = first.attributes['class'] ?? '';
      if (cls.startsWith('language-')) language = cls.substring('language-'.length);
    }
    var code = element.textContent;
    if (code.endsWith('\n')) code = code.substring(0, code.length - 1);
    return CodeBlock(code: code, language: language);
  }
}

/// A fenced code block: language label, copy button, horizontal scroll, monospace.
class CodeBlock extends StatefulWidget {
  const CodeBlock({super.key, required this.code, this.language = ''});
  final String code;
  final String language;

  @override
  State<CodeBlock> createState() => _CodeBlockState();
}

class _CodeBlockState extends State<CodeBlock> {
  bool _copied = false;
  Timer? _reset;

  @override
  void dispose() {
    _reset?.cancel();
    super.dispose();
  }

  Future<void> _copy() async {
    await Clipboard.setData(ClipboardData(text: widget.code));
    if (!mounted) return;
    setState(() => _copied = true);
    _reset?.cancel();
    _reset = Timer(const Duration(milliseconds: 1500), () {
      if (mounted) setState(() => _copied = false);
    });
    ScaffoldMessenger.maybeOf(context)
      ?..hideCurrentSnackBar()
      ..showSnackBar(const SnackBar(content: Text('Code copied'), duration: Duration(seconds: 2)));
  }

  @override
  Widget build(BuildContext context) {
    final pai = context.pai;
    final label = widget.language.isEmpty ? 'CODE' : widget.language.toUpperCase();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          padding: const EdgeInsets.only(left: 12),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: pai.line)),
          ),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: pai.mono(size: 11, color: pai.muted).copyWith(letterSpacing: 1.5),
                ),
              ),
              IconButton(
                tooltip: _copied ? 'Copied' : 'Copy code',
                onPressed: _copy,
                constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
                iconSize: 18,
                icon: Icon(_copied ? Icons.check : Icons.copy_all_outlined, color: _copied ? pai.green : pai.muted),
              ),
            ],
          ),
        ),
        Scrollbar(
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.all(12),
            child: Text(widget.code, softWrap: false, style: pai.mono(size: 13, strong: true).copyWith(height: 1.4)),
          ),
        ),
      ],
    );
  }
}

// =======================================================================================
// Remote images — never fetched until the user taps
// =======================================================================================

class RemoteImagePlaceholder extends StatefulWidget {
  const RemoteImagePlaceholder({
    super.key,
    required this.uri,
    this.alt,
    this.imageProviderFactory = _defaultImageProvider,
  });
  final Uri uri;
  final String? alt;
  final RemoteImageProviderFactory imageProviderFactory;

  @override
  State<RemoteImagePlaceholder> createState() => _RemoteImagePlaceholderState();
}

class _RemoteImagePlaceholderState extends State<RemoteImagePlaceholder> {
  bool _load = false;

  @override
  Widget build(BuildContext context) {
    final pai = context.pai;
    final safe = parseSafeImage(widget.uri);
    final alt = (widget.alt ?? '').trim();

    Widget chip(IconData icon, String text, {VoidCallback? onTap, String? semantics}) {
      final inner = Container(
        constraints: const BoxConstraints(minHeight: 48),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: pai.codeBg,
          borderRadius: BorderRadius.circular(PaiRadii.sm),
          border: Border.all(color: pai.line),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 18, color: pai.muted),
            const SizedBox(width: 8),
            Flexible(child: Text(text, maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(color: pai.strong))),
          ],
        ),
      );
      return Align(
        alignment: Alignment.centerLeft,
        child: Semantics(
          button: onTap != null,
          label: semantics ?? text,
          child: ExcludeSemantics(
            child: onTap == null
                ? inner
                : InkWell(borderRadius: BorderRadius.circular(PaiRadii.sm), onTap: onTap, child: inner),
          ),
        ),
      );
    }

    if (safe == null) {
      return chip(Icons.block, 'Image blocked (unsupported address)');
    }
    if (!_load) {
      final caption = alt.isEmpty ? '' : '$alt · ';
      return chip(
        Icons.image_outlined,
        'Image (tap to load) $caption${safe.host}',
        semantics: 'Load image from ${safe.host}',
        onTap: () => setState(() => _load = true),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ConstrainedBox(
          constraints: const BoxConstraints(maxHeight: 320),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(PaiRadii.sm),
            child: Image(
              image: widget.imageProviderFactory(safe),
              fit: BoxFit.contain,
              semanticLabel: alt.isEmpty ? 'Image from ${safe.host}' : alt,
              loadingBuilder: (c, child, p) => p == null
                  ? child
                  : const SizedBox(height: 48, child: Center(child: CircularProgressIndicator(strokeWidth: 2))),
              errorBuilder: (c, e, st) {
                debugPrint('[MarkdownMessage] image ${safe.host} failed to load: $e');
                return chip(Icons.broken_image_outlined, 'Image could not be loaded');
              },
            ),
          ),
        ),
        if (alt.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(alt, style: Theme.of(context).textTheme.bodySmall?.copyWith(color: pai.muted)),
          ),
      ],
    );
  }
}
