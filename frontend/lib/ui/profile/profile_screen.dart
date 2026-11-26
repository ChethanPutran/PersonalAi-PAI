import 'package:flutter/material.dart';
import 'package:pai_mobile/models/device.dart';

import '../../device/remote_access.dart';
import '../../models/user.dart';
import '../../profile/profile_service.dart';
import '../../profile/settings_service.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';

class ProfileScreen extends StatefulWidget {
  final ProfileService service;
  final SettingsService settings;
  final String? currentDeviceId;
  final Future<void> Function() onLogout;

  /// This device's remote-access switches. When null the "Remote access" section is hidden.
  final RemoteAccessController? remoteAccess;

  const ProfileScreen({
    super.key,
    required this.service,
    required this.settings,
    required this.onLogout,
    this.currentDeviceId,
    this.remoteAccess,
  });

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  UserProfile? _profile;
  List<DeviceInfo> _devices = const [];
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
    return HudScaffold(
      appBar: HudAppBar(
        title: 'Profile',
        automaticallyImplyLeading: false,
        actions: [
          HudIconButton(
            icon: Icons.refresh,
            tooltip: 'Refresh',
            onPressed: _loading ? null : _load,
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: RefreshIndicator(onRefresh: _load, child: _buildBody()),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const HudSkeletonList(count: 3);
    }
    if (_error != null) {
      return LayoutBuilder(
        builder: (context, box) => ListView(
          children: [
            SizedBox(
              height: box.maxHeight,
              child: HudErrorState(
                title: 'Failed to load profile',
                details: _error,
                onRetry: _load,
              ),
            ),
          ],
        ),
      );
    }

    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 860),
        child: ListView(
      children: [
        if (_profile != null) _ProfileHeader(profile: _profile!),

        _SectionHeader('My Devices (${_devices.length})'),
        if (_devices.isEmpty)
          Padding(
            padding: EdgeInsets.fromLTRB(16, 0, 16, 16),
            child: Text(
              'No devices registered to this account.',
              style: TextStyle(color: context.pai.muted),
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

        _SectionHeader('Settings'),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg),
          child: HudPanel(
            brackets: false,
            padding: const EdgeInsets.symmetric(vertical: PaiSpacing.xs),
            child: Column(
              children: [
                SwitchListTile(
                  secondary: const Icon(Icons.notifications_outlined),
                  title: const Text('Notifications'),
                  subtitle: const Text('Push alerts for task events'),
                  value: widget.settings.notificationsEnabled,
                  onChanged: widget.settings.setNotificationsEnabled,
                ),
                const Divider(),
                SwitchListTile(
                  secondary: const Icon(Icons.sync),
                  title: const Text('Auto-sync'),
                  subtitle: const Text('Keep plugins and sessions in sync'),
                  value: widget.settings.autoSyncEnabled,
                  onChanged: widget.settings.setAutoSyncEnabled,
                ),
                const Divider(),
                SwitchListTile(
                  secondary: const Icon(Icons.location_on_outlined),
                  title: const Text('Background location'),
                  subtitle: const Text('Allow location-aware capabilities'),
                  value: widget.settings.backgroundLocationEnabled,
                  onChanged: widget.settings.setBackgroundLocationEnabled,
                ),
              ],
            ),
          ),
        ),

        if (widget.remoteAccess != null) ...[
          _SectionHeader('Remote access'),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg),
            child: HudPanel(
              brackets: false,
              padding: const EdgeInsets.symmetric(vertical: PaiSpacing.xs),
              child: _RemoteAccessSwitches(controller: widget.remoteAccess!),
            ),
          ),
        ],

        const SizedBox(height: PaiSpacing.xl),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg),
          child: GlowButton(
            label: 'Sign out',
            icon: Icons.logout,
            variant: GlowVariant.danger,
            expanded: true,
            onPressed: () => _confirm(
              title: 'Sign out?',
              message: 'You will need to sign in again to use PAI.',
              confirmLabel: 'Sign out',
              destructive: true,
              action: widget.onLogout,
            ),
          ),
        ),
        const SizedBox(height: 32),
      ],
        ),
      ),
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
  Widget build(BuildContext context) => HudSectionHeader(title);
}

class _ProfileHeader extends StatelessWidget {
  final UserProfile profile;
  const _ProfileHeader({required this.profile});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, PaiSpacing.lg, PaiSpacing.lg, 0),
      child: HudPanel(
        blur: true,
        padding: const EdgeInsets.all(PaiSpacing.lg),
        child: Row(
          children: [
            Container(
              width: 68,
              height: 68,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: theme.colorScheme.primaryContainer,
                border: Border.all(color: theme.colorScheme.primary.withValues(alpha: 0.8), width: 1.5),
                boxShadow: [BoxShadow(color: theme.colorScheme.primary.withValues(alpha: 0.3), blurRadius: 18, spreadRadius: -2)],
              ),
              child: Text(
                profile.initials,
                style: TextStyle(
                  fontFamily: PaiFonts.display,
                  fontSize: 24,
                  fontWeight: FontWeight.w700,
                  color: theme.colorScheme.primary,
                ),
              ),
            ),
            const SizedBox(width: PaiSpacing.lg),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    profile.email,
                    style: theme.textTheme.titleMedium,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 8,
                    runSpacing: 4,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      if (profile.isAdmin)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                          decoration: BoxDecoration(
                            color: PaiColors.amber.withValues(alpha: 0.12),
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(color: PaiColors.amber.withValues(alpha: 0.6)),
                          ),
                          child: Text(
                            'ADMIN',
                            style: theme.textTheme.labelSmall?.copyWith(color: PaiColors.amber, letterSpacing: 1.6),
                          ),
                        ),
                      if (profile.createdAt != null)
                        Text(
                          'Member since ${_fmtDate(profile.createdAt!)}',
                          style: theme.textTheme.bodySmall,
                        ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
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
  final DeviceInfo device;
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

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg, vertical: PaiSpacing.xs + 2),
      child: HudPanel(
      brackets: false,
      padding: EdgeInsets.zero,
      accent: d.isCurrent ? theme.colorScheme.primary : null,
      selected: d.isCurrent,
      child: Column(
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
                              style: theme.textTheme.titleMedium,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          const SizedBox(width: 8),
                          StatusChip(
                            status: d.connected ? HudStatus.online : HudStatus.offline,
                            compact: true,
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
                                'THIS DEVICE',
                                style: theme.textTheme.labelSmall?.copyWith(
                                  fontSize: 10.5,
                                  letterSpacing: 1.2,
                                  color: theme.colorScheme.primary,
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
                        style: theme.textTheme.bodySmall,
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
            color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.3),
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
    ),
      ),
    );
  }

  Widget _deviceIcon(DeviceInfo d, ThemeData theme) {
    final p = d.platform.toLowerCase();
    IconData icon;
    if (p.contains('ios') || p.contains('android')) {
      icon = Icons.smartphone;
    } else if (p.contains('macos') ||
        p.contains('windows') ||
        p.contains('linux')) {
      icon = Icons.laptop;
    } else if (d.deviceType.toLowerCase().contains('mobile')) {
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

/// The two switches that decide what your OTHER devices (and the web, schedules, agents) may do to
/// THIS device. Both are off until you turn them on here; the device enforces them itself.
class _RemoteAccessSwitches extends StatelessWidget {
  final RemoteAccessController controller;
  const _RemoteAccessSwitches({required this.controller});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final enabled = !controller.saving;
        return Column(
          children: [
            SwitchListTile(
              key: const ValueKey('remote-install-switch'),
              secondary: const Icon(Icons.download_for_offline_outlined),
              title: const Text('Allow remote plugin install'),
              subtitle: const Text('Let your other devices install and remove plugins on this one'),
              value: controller.value.install,
              onChanged: enabled ? controller.setInstall : null,
            ),
            const Divider(),
            SwitchListTile(
              key: const ValueKey('remote-invoke-switch'),
              secondary: const Icon(Icons.settings_remote_outlined),
              title: const Text('Allow remote plugin use'),
              subtitle: const Text('Let your other devices, schedules and agents run plugins on this one'),
              value: controller.value.invoke,
              onChanged: enabled ? controller.setInvoke : null,
            ),
            if (controller.error != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, PaiSpacing.xs, PaiSpacing.lg, PaiSpacing.sm),
                child: Text(
                  controller.error!,
                  key: const ValueKey('remote-access-error'),
                  style: TextStyle(color: Theme.of(context).colorScheme.error, fontSize: 12),
                ),
              ),
          ],
        );
      },
    );
  }
}
