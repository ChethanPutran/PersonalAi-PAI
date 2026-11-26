import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../config/server_address.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';

/// "PAI server": the backend address this app talks to (IP or host, port, http/https).
///
/// Nothing is saved until the address is valid AND a connection test passed (or the user chose to save anyway).
/// [onApply] receives the cleaned URL, or null for "reset to default"; it persists, reconnects and, since an
/// account belongs to one server, signs the user out when the server changed (the section says so first).
class ServerSection extends StatefulWidget {
  /// The address in use now (host-only URL).
  final String currentUrl;

  /// What "Reset to default" goes back to.
  final String defaultUrl;
  final bool usingCustom;

  /// True when a user is signed in: changing the server then signs them out, which is explained in a dialog.
  final bool signedIn;
  final Future<void> Function(String? url) onApply;

  /// Test seam: the HTTP client the connection test uses.
  final http.Client? client;

  const ServerSection({
    super.key,
    required this.currentUrl,
    required this.defaultUrl,
    required this.usingCustom,
    required this.signedIn,
    required this.onApply,
    this.client,
  });

  @override
  State<ServerSection> createState() => _ServerSectionState();
}

class _ServerSectionState extends State<ServerSection> {
  late final TextEditingController _ctrl = TextEditingController(text: widget.currentUrl);
  bool _testing = false;
  bool _saving = false;
  ConnectionTest? _test;
  String? _testedUrl;

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  ServerAddressResult get _parsed => ServerAddress.parse(_ctrl.text);

  String? get _inputError {
    if (_ctrl.text.trim().isEmpty) return null;                // nothing typed yet: no red text
    return _parsed.error;
  }

  bool get _changed => _parsed.ok && _parsed.address!.url != widget.currentUrl;

  Future<void> _runTest() async {
    final r = _parsed;
    if (!r.ok) {
      setState(() => _test = ConnectionTest(false, r.error!));
      return;
    }
    final url = r.address!.url;
    setState(() {
      _testing = true;
      _test = null;
    });
    final t = await testServerConnection(url, client: widget.client);
    if (!mounted) return;
    setState(() {
      _testing = false;
      _test = t;
      _testedUrl = url;
    });
  }

  Future<void> _save() async {
    final r = _parsed;
    if (!r.ok) {
      setState(() => _test = ConnectionTest(false, r.error!));
      return;
    }
    final addr = r.address!;
    if (_testedUrl != addr.url || _test?.ok != true) await _runTest();
    if (!mounted) return;
    final reachable = _test?.ok == true && _testedUrl == addr.url;
    final ok = await _confirm(addr, reachable);
    if (ok != true || !mounted) return;
    setState(() => _saving = true);
    try {
      await widget.onApply(addr.url);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _reset() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Use the default server?'),
        content: Text('The app goes back to ${widget.defaultUrl}.${widget.signedIn ? ' You will be signed out, because your account belongs to the server you are leaving.' : ''}'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(key: const Key('server-reset-confirm'), onPressed: () => Navigator.pop(ctx, true), child: const Text('Reset')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _saving = true);
    try {
      await widget.onApply(null);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<bool?> _confirm(ServerAddress addr, bool reachable) {
    final lines = <String>[
      if (!reachable) "The test did not succeed (${_test?.message ?? 'no result'}). You can still save it, for example if the server is not running yet.",
      if (addr.warning != null) addr.warning!,
      if (widget.signedIn)
        'You will be signed out. An account belongs to one server, so the sign-in from ${widget.currentUrl} does not work on ${addr.url}. Sign in again afterwards.',
    ];
    if (lines.isEmpty) return Future.value(true);
    return showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Switch to ${addr.url}?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [for (final l in lines) Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(l))],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(key: const Key('server-save-confirm'), onPressed: () => Navigator.pop(ctx, true), child: const Text('Switch server')),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final parsed = _parsed;
    final warning = parsed.ok ? parsed.address!.warning : null;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg),
      child: HudPanel(
        brackets: false,
        padding: const EdgeInsets.all(PaiSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'The address of the PAI server this app talks to. Use the computer\'s IP (like 192.168.1.20) or a host name; '
              'the port defaults to ${ServerAddress.defaultPort}. Now using ${widget.currentUrl}${widget.usingCustom ? '' : ' (default)'}.',
              style: text.bodySmall,
            ),
            const SizedBox(height: PaiSpacing.sm),
            HudTextField(
              key: const Key('server-field'),
              controller: _ctrl,
              label: 'PAI server',
              hint: 'http://192.168.1.20:8000',
              icon: Icons.dns_outlined,
              keyboardType: TextInputType.url,
              textInputAction: TextInputAction.done,
              errorText: _inputError,
              onChanged: (_) => setState(() {
                _test = null;
              }),
              onSubmitted: (_) => _runTest(),
            ),
            if (warning != null)
              Padding(
                padding: const EdgeInsets.only(top: PaiSpacing.sm),
                child: Row(
                  key: const Key('server-http-warning'),
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(Icons.warning_amber_rounded, size: 16, color: scheme.tertiary),
                    const SizedBox(width: 6),
                    Expanded(child: Text(warning, style: text.bodySmall)),
                  ],
                ),
              ),
            if (_test != null)
              Padding(
                padding: const EdgeInsets.only(top: PaiSpacing.sm),
                child: Row(
                  key: const Key('server-test-result'),
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(_test!.ok ? Icons.check_circle_outline : Icons.error_outline,
                        size: 16, color: _test!.ok ? scheme.primary : scheme.error),
                    const SizedBox(width: 6),
                    Expanded(
                        child: Text(_test!.message,
                            style: text.bodySmall?.copyWith(color: _test!.ok ? scheme.primary : scheme.error))),
                  ],
                ),
              ),
            const SizedBox(height: PaiSpacing.md),
            Wrap(
              spacing: PaiSpacing.sm,
              runSpacing: PaiSpacing.sm,
              children: [
                OutlinedButton.icon(
                  key: const Key('server-test'),
                  onPressed: _testing || _saving ? null : _runTest,
                  icon: _testing
                      ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.wifi_tethering, size: 18),
                  label: const Text('Test connection'),
                ),
                FilledButton.icon(
                  key: const Key('server-save'),
                  onPressed: _changed && !_testing && !_saving ? _save : null,
                  icon: const Icon(Icons.check, size: 18),
                  label: Text(_saving ? 'Saving…' : 'Save'),
                ),
                if (widget.usingCustom)
                  TextButton(
                    key: const Key('server-reset'),
                    onPressed: _saving ? null : _reset,
                    child: const Text('Reset to default'),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
