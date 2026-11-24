import 'package:flutter/material.dart';

import '../../../plugins/permissions/permission_models.dart';
import '../../../plugins/permissions/permissions_service.dart';
import '../../theme/theme.dart';
import '../../widgets/hud/hud.dart';
import 'permission_widgets.dart';

/// What the user has said "allow for this session" / "always allow" to — with Revoke.
///
/// Reached from the Plugins screen and from the plugin tester sheet.
class PermissionsScreen extends StatefulWidget {
  const PermissionsScreen({super.key, required this.service, this.deviceNames = const {}});

  final PermissionsService service;

  /// device id -> friendly name, so grants read "Pixel 8" instead of a uuid.
  final Map<String, String> deviceNames;

  @override
  State<PermissionsScreen> createState() => _PermissionsScreenState();
}

class _PermissionsScreenState extends State<PermissionsScreen> {
  List<ConsentGrant>? _grants;
  Object? _error;
  bool _loading = false;
  final Set<String> _revoking = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final list = await widget.service.listConsents();
      if (!mounted) return;
      setState(() => _grants = [
            for (final g in list) g.withDeviceName(widget.deviceNames[g.deviceId] ?? g.deviceName),
          ]);
    } catch (e) {
      debugPrint('[PermissionsScreen] load failed: ${e.runtimeType}: $e');
      if (mounted) setState(() => _error = e);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _revoke(ConsentGrant g) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Revoke this permission?'),
        content: Text(
          '${g.capability} on ${g.deviceLabel} will ask for your approval again the next time '
          'it runs.',
        ),
        actions: [
          TextButton(
            key: const Key('revoke-cancel'),
            autofocus: true,
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Keep it'),
          ),
          FilledButton(
            key: const Key('revoke-confirm'),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Revoke'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;

    // Remove it from the list at once; put it back if the server refuses.
    final before = List<ConsentGrant>.of(_grants ?? const []);
    setState(() {
      _grants = before.where((x) => x.id != g.id).toList();
      _revoking.add(g.id);
    });
    try {
      await widget.service.revoke(g.id);
    } catch (e) {
      debugPrint('[PermissionsScreen] revoke ${g.id} failed: ${e.runtimeType}: $e');
      if (!mounted) return;
      setState(() => _grants = before);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not revoke ${g.capability}: ${_message(e)}')),
      );
    } finally {
      if (mounted) setState(() => _revoking.remove(g.id));
    }
  }

  static String _message(Object e) {
    final s = e.toString();
    return s.length > 160 ? '${s.substring(0, 157)}…' : s;
  }

  // ------------------------------------------------------------------- build

  @override
  Widget build(BuildContext context) {
    return HudScaffold(
      appBar: HudAppBar(
        title: 'Permissions',
        actions: [
          IconButton(
            tooltip: 'Reload permissions',
            icon: const Icon(Icons.refresh),
            onPressed: _loading ? null : _load,
          ),
        ],
      ),
      body: _body(context),
    );
  }

  Widget _body(BuildContext context) {
    if (_grants == null && _error != null) {
      return HudErrorState(
        title: 'Could not load permissions',
        message: 'Check your connection and try again.',
        details: _message(_error!),
        onRetry: _load,
      );
    }
    if (_grants == null) return const HudSkeletonList(count: 4);
    final grants = _grants!;
    if (grants.isEmpty) {
      return const HudEmptyState(
        icon: Icons.verified_user_outlined,
        title: 'No saved permissions',
        message: 'When you choose “Allow for this session” or “Always allow”, it shows up '
            'here so you can revoke it at any time.',
      );
    }

    final byPlugin = <String, List<ConsentGrant>>{};
    for (final g in grants) {
      byPlugin.putIfAbsent(g.pluginId.isEmpty ? 'Other' : g.pluginId, () => []).add(g);
    }
    final plugins = byPlugin.keys.toList()..sort();

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.only(bottom: PaiSpacing.xl),
        children: [
          for (final plugin in plugins) ..._pluginSection(context, plugin, byPlugin[plugin]!),
        ],
      ),
    );
  }

  List<Widget> _pluginSection(BuildContext context, String plugin, List<ConsentGrant> items) {
    final byCap = <String, List<ConsentGrant>>{};
    for (final g in items) {
      byCap.putIfAbsent(g.capability, () => []).add(g);
    }
    final caps = byCap.keys.toList()..sort();
    return [
      HudSectionHeader(
        plugin,
        trailing: Text('${items.length}', style: PaiText.mono(size: 12, color: context.pai.muted)),
      ),
      for (final cap in caps) ...[
        Padding(
          padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, PaiSpacing.xs, PaiSpacing.lg, PaiSpacing.xs),
          child: Text(cap, style: PaiText.mono(size: 12, color: context.pai.muted)),
        ),
        for (final g in (byCap[cap]!..sort((a, b) => a.deviceLabel.compareTo(b.deviceLabel))))
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg, vertical: PaiSpacing.xs),
            child: _GrantCard(
              grant: g,
              busy: _revoking.contains(g.id),
              onRevoke: () => _revoke(g),
            ),
          ),
      ],
    ];
  }
}

class _GrantCard extends StatelessWidget {
  const _GrantCard({required this.grant, required this.busy, required this.onRevoke});
  final ConsentGrant grant;
  final bool busy;
  final VoidCallback onRevoke;

  String get _expiry {
    final e = grant.expiresAt;
    if (e != null) return 'expires ${shortDate(e)}';
    return grant.scope == ConfirmationScope.session ? 'ends with the session' : 'no expiry';
  }

  String get _usage {
    final used = grant.lastUsedAt == null ? 'never used' : 'last used ${relativeTime(grant.lastUsedAt!)}';
    final n = grant.useCount;
    final times = n == 1 ? 'used once' : 'used $n times';
    return grant.lastUsedAt == null && n == 0 ? used : '$used · $times';
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: context.pai.muted);
    return HudPanel(
      key: Key('grant-${grant.id}'),
      padding: const EdgeInsets.all(PaiSpacing.md),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: Text(grant.deviceLabel, style: text.titleSmall)),
          ScopeChip(grant.scope),
        ]),
        const SizedBox(height: PaiSpacing.xs),
        Text(grant.bindingSummary,
            key: Key('binding-${grant.id}'), style: PaiText.mono(size: 12, color: context.pai.strong)),
        const SizedBox(height: PaiSpacing.xs),
        Text(_expiry, key: Key('expiry-${grant.id}'), style: muted),
        Text(_usage, key: Key('usage-${grant.id}'), style: muted),
        const SizedBox(height: PaiSpacing.sm),
        Row(children: [
          RiskBadge(grant.risk),
          const Spacer(),
          TextButton.icon(
            key: Key('revoke-${grant.id}'),
            style: TextButton.styleFrom(minimumSize: const Size(48, 48)),
            onPressed: busy ? null : onRevoke,
            icon: busy
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : Icon(Icons.block, size: 18, color: context.pai.red),
            label: Text('Revoke', style: TextStyle(color: context.pai.red)),
          ),
        ]),
      ]),
    );
  }
}
