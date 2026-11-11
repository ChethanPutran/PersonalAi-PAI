import 'package:flutter/material.dart';

import '../../models/user_device.dart';
import '../../models/user_profile.dart';
import '../../profile/profile_service.dart';
import '../../profile/settings_service.dart';

class ProfileScreen extends StatefulWidget {
  final ProfileService service;
  final SettingsService settings;
  final String? currentDeviceId;
  final Future<void> Function() onLogout;

  const ProfileScreen({
    super.key,
    required this.service,
    required this.settings,
    required this.onLogout,
    this.currentDeviceId,
  });

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  UserProfile? _profile;
  List<UserDevice> _devices = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    widget.settings.addListener(_onSettings);
    _load();
  }

  @override
  void dispose() {
    widget.settings.removeListener(_onSettings);
    super.dispose();
  }

  void _onSettings() {
    if (mounted) setState(() {});
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      // Sequential: /auth/me gives us the user_id that
      // /devices/list/{user_id} needs.
      final profile = await widget.service.getProfile();
      final devices = await widget.service.getDevices(
        userId: profile.id,
        currentDeviceId: widget.currentDeviceId,
      );
      if (!mounted) return;
      setState(() {
        _profile = profile;
        _devices = devices;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  // ---------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------

  Future<void> _confirm({
    required String title,
    required String message,
    required String confirmLabel,
    required Future<void> Function() action,
    bool destructive = false,
  }) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Text(message),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: destructive
                ? FilledButton.styleFrom(
                    backgroundColor: Theme.of(ctx).colorScheme.error,
                  )
                : null,
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(confirmLabel),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await action();
      await _load();
    } catch (e) {
      if (!mounted) return;
      _snack('Action failed: $e');
    }
  }

  void _snack(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  // ---------------------------------------------------------------
  // Build
  // ---------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Profile'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _loading ? null : _load,
          ),
        ],
      ),
      body: RefreshIndicator(onRefresh: _load, child: _buildBody()),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return ListView(
        padding: const EdgeInsets.all(24),
        children: [
          const Icon(Icons.error_outline, size: 48, color: Colors.red),
          const SizedBox(height: 16),
          Text('Failed to load profile\n\n$_error',
              textAlign: TextAlign.center),
          const SizedBox(height: 24),
          Center(
            child: ElevatedButton(onPressed: _load, child: const Text('Retry')),
          ),
        ],
      );
    }

    return ListView(
      children: [
        if (_profile != null) _ProfileHeader(profile: _profile!),
        const Divider(height: 1),

        _SectionHeader('My Devices (${_devices.length})'),
        if (_devices.isEmpty)
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 0, 16, 16),
            child: Text(
              'No devices registered to this account.',
              style: TextStyle(color: Colors.grey),
            ),
          )
        else
          ..._devices.map((d) => _DeviceTile(
                device: d,
                settings: widget.settings,
                onDisconnect: (!d.isCurrent && d.connected)
                    ? () => _confirm(
                          title: 'Disconnect device?',
                          message:
                              '"${d.name}" will be signed out of the live '
                              'WebSocket. The device stays registered and '
                              'can reconnect later.',
                          confirmLabel: 'Disconnect',
                          action: () => widget.service.disconnectDevice(d.id),
                        )
                    : null,
                onRemove: !d.isCurrent
                    ? () => _confirm(
                          title: 'Remove device?',
                          message:
                              '"${d.name}" will be permanently unregistered '
                              'from this account. If the app is still running '
                              'on it, it will re-register under a new id on '
                              'next launch.',
                          confirmLabel: 'Remove',
                          destructive: true,
                          action: () async {
                            await widget.service.removeDevice(d.id);
                            await widget.settings.forgetDevice(d.id);
                          },
                        )
                    : null,
              )),

        const Divider(height: 1),

        _SectionHeader('Settings'),
        SwitchListTile(
          secondary: const Icon(Icons.notifications_outlined),
          title: const Text('Notifications'),
          subtitle: const Text('Push alerts for task events'),
          value: widget.settings.notificationsEnabled,
          onChanged: widget.settings.setNotificationsEnabled,
        ),
        SwitchListTile(
          secondary: const Icon(Icons.sync),
          title: const Text('Auto-sync'),
          subtitle: const Text('Keep plugins and sessions in sync'),
          value: widget.settings.autoSyncEnabled,
          onChanged: widget.settings.setAutoSyncEnabled,
        ),
        SwitchListTile(
          secondary: const Icon(Icons.location_on_outlined),
          title: const Text('Background location'),
          subtitle: const Text('Allow location-aware capabilities'),
          value: widget.settings.backgroundLocationEnabled,
          onChanged: widget.settings.setBackgroundLocationEnabled,
        ),

        const Divider(height: 1),
        const SizedBox(height: 16),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: FilledButton.tonalIcon(
            onPressed: () => _confirm(
              title: 'Sign out?',
              message: 'You will need to sign in again to use PAI.',
              confirmLabel: 'Sign out',
              destructive: true,
              action: widget.onLogout,
            ),
            icon: const Icon(Icons.logout),
            label: const Text('Sign out'),
            style: FilledButton.styleFrom(
              minimumSize: const Size.fromHeight(48),
            ),
          ),
        ),
        const SizedBox(height: 32),
      ],
    );
  }
}

// -----------------------------------------------------------------
// Sub-widgets
// -----------------------------------------------------------------

class _SectionHeader extends StatelessWidget {
  final String title;
  const _SectionHeader(this.title);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 8),
      child: Text(
        title.toUpperCase(),
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.8,
          color: Theme.of(context).colorScheme.primary,
        ),
      ),
    );
  }
}

class _ProfileHeader extends StatelessWidget {
  final UserProfile profile;
  const _ProfileHeader({required this.profile});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 24, 16, 24),
      child: Row(
        children: [
          CircleAvatar(
            radius: 32,
            backgroundColor: theme.colorScheme.primaryContainer,
            child: Text(
              profile.initials,
              style: TextStyle(
                fontSize: 26,
                fontWeight: FontWeight.w600,
                color: theme.colorScheme.onPrimaryContainer,
              ),
            ),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  profile.email,
                  style: theme.textTheme.titleMedium
                      ?.copyWith(fontWeight: FontWeight.w600),
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 4),
                Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    if (profile.isAdmin)
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 2),
                        decoration: BoxDecoration(
                          color: theme.colorScheme.tertiaryContainer,
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(
                          'Admin',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            color: theme.colorScheme.onTertiaryContainer,
                          ),
                        ),
                      ),
                    if (profile.createdAt != null)
                      Text(
                        'Member since ${_fmtDate(profile.createdAt!)}',
                        style: theme.textTheme.bodySmall
                            ?.copyWith(color: theme.colorScheme.outline),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _fmtDate(DateTime d) {
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    return '${months[d.month - 1]} ${d.year}';
  }
}

/// One device row with an expandable settings panel.
///
/// Actions are driven entirely by the callbacks: if `onDisconnect` /
/// `onRemove` are null, the corresponding button is hidden. No extra
/// permission flag is needed — the parent decides who can manage what.
class _DeviceTile extends StatefulWidget {
  final UserDevice device;
  final SettingsService settings;
  final VoidCallback? onDisconnect;
  final VoidCallback? onRemove;

  const _DeviceTile({
    required this.device,
    required this.settings,
    this.onDisconnect,
    this.onRemove,
  });

  @override
  State<_DeviceTile> createState() => _DeviceTileState();
}

class _DeviceTileState extends State<_DeviceTile> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final d = widget.device;
    final theme = Theme.of(context);
    final hasActions =
        widget.onDisconnect != null || widget.onRemove != null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        InkWell(
          onTap: () => setState(() => _expanded = !_expanded),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
            child: Row(
              children: [
                _deviceIcon(d, theme),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(
                              d.name,
                              style: theme.textTheme.titleSmall
                                  ?.copyWith(fontWeight: FontWeight.w600),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          if (d.isCurrent) ...[
                            const SizedBox(width: 8),
                            Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 6, vertical: 1),
                              decoration: BoxDecoration(
                                color: theme.colorScheme.primaryContainer,
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text(
                                'This device',
                                style: TextStyle(
                                  fontSize: 10,
                                  fontWeight: FontWeight.w600,
                                  color: theme.colorScheme.onPrimaryContainer,
                                ),
                              ),
                            ),
                          ],
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text(
                        [
                          if (d.subtitleParts.isNotEmpty) d.subtitleParts,
                          d.lastSeenLabel,
                        ].join(' · '),
                        style: theme.textTheme.bodySmall
                            ?.copyWith(color: theme.colorScheme.outline),
                      ),
                    ],
                  ),
                ),
                Icon(
                  _expanded ? Icons.expand_less : Icons.expand_more,
                  color: theme.colorScheme.outline,
                ),
              ],
            ),
          ),
        ),
        if (_expanded)
          Container(
            color: theme.colorScheme.surfaceContainerHighest.withOpacity(0.3),
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
            child: Column(
              children: [
                SwitchListTile.adaptive(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  title: const Text('Notifications'),
                  value: widget.settings.getDeviceNotifications(d.id),
                  onChanged: (v) =>
                      widget.settings.setDeviceNotifications(d.id, v),
                ),
                SwitchListTile.adaptive(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  title: const Text('Auto-connect on startup'),
                  value: widget.settings.getDeviceAutoConnect(d.id),
                  onChanged: (v) =>
                      widget.settings.setDeviceAutoConnect(d.id, v),
                ),
                if (hasActions) ...[
                  const SizedBox(height: 4),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.end,
                    children: [
                      if (widget.onDisconnect != null)
                        TextButton.icon(
                          onPressed: widget.onDisconnect,
                          icon: const Icon(Icons.link_off, size: 18),
                          label: const Text('Disconnect'),
                        ),
                      if (widget.onRemove != null)
                        TextButton.icon(
                          onPressed: widget.onRemove,
                          icon: Icon(Icons.delete_outline,
                              size: 18, color: theme.colorScheme.error),
                          label: Text(
                            'Remove',
                            style: TextStyle(color: theme.colorScheme.error),
                          ),
                        ),
                    ],
                  ),
                ],
              ],
            ),
          ),
      ],
    );
  }

  Widget _deviceIcon(UserDevice d, ThemeData theme) {
    final p = (d.platform ?? '').toLowerCase();
    IconData icon;
    if (p.contains('ios') || p.contains('android')) {
      icon = Icons.smartphone;
    } else if (p.contains('macos') ||
        p.contains('windows') ||
        p.contains('linux')) {
      icon = Icons.laptop;
    } else if ((d.deviceType ?? '').toLowerCase().contains('mobile')) {
      icon = Icons.smartphone;
    } else {
      icon = Icons.devices_other;
    }

    return Container(
      width: 40,
      height: 40,
      decoration: BoxDecoration(
        color: d.connected
            ? theme.colorScheme.primaryContainer
            : theme.colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Icon(
        icon,
        color: d.connected
            ? theme.colorScheme.onPrimaryContainer
            : theme.colorScheme.outline,
        size: 22,
      ),
    );
  }
}