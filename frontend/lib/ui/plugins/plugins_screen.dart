import 'package:flutter/material.dart';
import 'package:pai_mobile/device/repositories/device_repository.dart';
import 'package:pai_mobile/models/device.dart';
import 'package:pai_mobile/plugins/plugin_repository.dart';
import 'package:pai_mobile/plugins/plugin_service.dart';
import '../../core/app_version.dart';
import '../../plugins/models/plugin_info.dart';
import '../../plugins/models/plugin_update.dart';
import 'widgets/plugin_update_sheet.dart';
import '../../plugins/plugin_manager.dart';
import 'capability_config_screen.dart';
import 'plugin_test_screen.dart';
import '../../plugins/permissions/permissions_service.dart';
import 'permissions/permissions_screen.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';

class PluginsScreen extends StatefulWidget {
  final PluginManager manager;
  final DeviceRepository deviceRepository;
  final DeviceInfo? thisDevice;

  const PluginsScreen({
    super.key,
    required this.manager,
    required this.deviceRepository,
    required this.thisDevice,
  });

  @override
  State<PluginsScreen> createState() => _PluginsScreenState();
}

class _SectionHeader extends StatelessWidget {
  final String title;
  final String? subtitle;
  const _SectionHeader({required this.title, this.subtitle});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        HudSectionHeader(title),
        if (subtitle != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, 0, PaiSpacing.lg, PaiSpacing.sm),
            child: Text(subtitle!, style: context.pai.mono(size: 12)),
          ),
      ],
    );
  }
}

/// One plugin as a HUD panel. Same slots as a ListTile (leading / title / subtitle / trailing),
/// but the actions drop under the text on narrow screens instead of squeezing the title.
class _PluginRow extends StatelessWidget {
  final Widget? leading;
  final Widget title;
  final Widget? subtitle;
  final Widget? trailing;
  final bool isThreeLine;
  const _PluginRow({this.leading, required this.title, this.subtitle, this.trailing, this.isThreeLine = false});

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final info = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        DefaultTextStyle.merge(style: text.titleMedium, child: title),
        if (subtitle != null) ...[
          const SizedBox(height: 3),
          DefaultTextStyle.merge(style: text.bodySmall, child: subtitle!),
        ],
      ],
    );
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.lg, vertical: PaiSpacing.xs + 2),
      child: HudPanel(
        brackets: false,
        padding: const EdgeInsets.fromLTRB(PaiSpacing.md, PaiSpacing.md, PaiSpacing.md, PaiSpacing.sm),
        child: LayoutBuilder(
          builder: (context, box) {
            final head = Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (leading != null) ...[
                  Padding(padding: const EdgeInsets.only(top: 2), child: leading),
                  const SizedBox(width: PaiSpacing.md),
                ],
                Expanded(child: info),
              ],
            );
            if (trailing == null) return head;
            if (box.maxWidth >= 600) {
              return Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [Expanded(child: head), const SizedBox(width: PaiSpacing.md), trailing!],
              );
            }
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [head, Align(alignment: Alignment.centerRight, child: trailing!)],
            );
          },
        ),
      ),
    );
  }
}

class _PluginsScreenState extends State<PluginsScreen> {
  final Set<String> _installing = {};

  // ---------------------------------------------------------------------------
  // Install — with permission-consent flow
  // ---------------------------------------------------------------------------
Future<void> _handleInstall(String pluginId, String version) async {
  final plugin = widget.manager.get(pluginId);
  if (plugin == null) return;

  setState(() => _installing.add(pluginId));
  try {
    await widget.manager.install(pluginId, version);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Plugin installed')),
    );
  } on PluginPermissionDeniedException catch (e) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(
        '${plugin.name}: permission denied '
        '(${e.denied.join(", ")}). Tap Enable to retry.',
      ),
    ));
  } on PluginPermissionPendingException catch (e) {
    // Only reachable if consent is wired but the runtime still reports
    // pending after grant — e.g. flaky OS handler.
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(
        '${plugin.name}: consent pending for ${e.pending.join(", ")}',
      ),
    ));
  } on PluginRepositoryException catch (e) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Install failed: $e')),
    );
  } catch (e) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Install failed: $e')),
    );
  } finally {
    if (mounted) setState(() => _installing.remove(pluginId));
  }
}


  // ---------------------------------------------------------------------------
  // Update
  // ---------------------------------------------------------------------------

  /// Shows what the update changes first; only installs if the user confirms.
  Future<void> _showUpdate(PluginInfo plugin, {String? deviceId, String? deviceName}) async {
    final update = widget.manager.updateFor(plugin.id, deviceId: deviceId);
    if (update == null) return;
    final go = await showPluginUpdateSheet(context, plugin: plugin, update: update);
    if (go != true || !mounted) return;

    final messenger = ScaffoldMessenger.of(context);
    setState(() => _installing.add(plugin.id));
    try {
      if (deviceId == null) {
        await widget.manager.update(plugin.id);
        messenger.showSnackBar(SnackBar(content: Text('${plugin.name} updated to v${update.latestVersion}')));
      } else {
        await widget.manager.updateOnDevice(plugin.id, deviceId);
        messenger.showSnackBar(SnackBar(content: Text('Update requested on ${deviceName ?? 'device'}')));
      }
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Update failed: $e')));
    } finally {
      if (mounted) setState(() => _installing.remove(plugin.id));
    }
  }

  /// "v1.0.0" or "v1.0.0 → v2.0.0" plus a tappable hint when a newer version is blocked.
  Widget _versionLine(PluginInfo plugin, PluginUpdate? update, String? installed, List<String> caps) {
    final shown = installed ?? plugin.version;
    final label = (update?.updateAvailable ?? false)
        ? 'v$shown → v${update!.latestVersion}'
        : 'v$shown';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('$label  ·  ${caps.join(", ")}'),
        if (update?.blocked != null)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(
              'v${update!.blocked!.version} needs ${update.blocked!.required ?? 'a newer version'}',
              style: TextStyle(fontSize: 11, color: context.pai.muted),
            ),
          ),
      ],
    );
  }

  // ---------------------------------------------------------------------------
  // Uninstall (unchanged)
  // ---------------------------------------------------------------------------

  Future<void> _uninstall(String id) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.manager.uninstall(id);
      if (!mounted) return;
      messenger.showSnackBar(
        const SnackBar(content: Text('Plugin uninstalled')),
      );
    } catch (e) {
      if (!mounted) return;
      messenger.showSnackBar(SnackBar(content: Text('Uninstall failed: $e')));
    }
  }

  // ---------------------------------------------------------------------------
  // Build (unchanged except Install button now calls _handleInstall)
  // ---------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return HudScaffold(
      appBar: HudAppBar(
        title: 'Plugins',
        automaticallyImplyLeading: false,
        actions: [
          IconButton(
            key: const Key('open-permissions'),
            tooltip: 'Permissions',
            icon: const Icon(Icons.verified_user_outlined),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => PermissionsScreen(
                  service: PermissionsService.of(widget.manager.api),
                  deviceNames: {
                    if (widget.thisDevice != null) widget.thisDevice!.id: widget.thisDevice!.name,
                  },
                ),
              ),
            ),
          ),
        ],
      ),
      body: ListenableBuilder(
        listenable: widget.manager,
        builder: (context, _) {
          final thisDevicePlugins = widget.manager.thisDevicePlugins;
          final otherGroups = widget.manager.otherDeviceGroups;

          if (thisDevicePlugins.isEmpty && otherGroups.isEmpty) {
            return RefreshIndicator(
              onRefresh: widget.manager.loadCatalog,
              child: LayoutBuilder(
                builder: (context, box) => ListView(
                  physics: const AlwaysScrollableScrollPhysics(),
                  children: [
                    SizedBox(
                      height: box.maxHeight,
                      child: const HudEmptyState(
                        icon: Icons.extension_outlined,
                        title: 'No plugins',
                        message: 'No plugins compatible with your devices.',
                      ),
                    ),
                  ],
                ),
              ),
            );
          }

          return RefreshIndicator(
            onRefresh: widget.manager.loadCatalog,
            child: Align(
              alignment: Alignment.topCenter,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 860),
                child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: [
                _SectionHeader(
                  title: 'This device',
                  subtitle:
                      '${widget.thisDevice?.name} · ${widget.thisDevice?.platform}/${widget.thisDevice?.architecture}',
                ),
                if (thisDevicePlugins.isEmpty)
                  Padding(
                    padding: EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                    child: Text(
                      'No plugins available for this device.',
                      style: TextStyle(color: context.pai.muted),
                    ),
                  )
                else
                  ...thisDevicePlugins.map((p) => _buildThisDeviceTile(p)),

                if (otherGroups.isNotEmpty) ...[
                  const SizedBox(height: 16),
                  const _SectionHeader(title: 'Your other devices'),
                  for (final group in otherGroups) ...[
                    Builder(
                      builder: (context) {
                        final device = widget.deviceRepository.getDevice(
                          group.deviceId,
                        );

                        if (device == null) {
                          return ListTile(
                            title: Text('Unknown device (${group.deviceId})'),
                          );
                        }

                        return Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Padding(
                              padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
                              child: Row(
                                children: [
                                  Expanded(
                                    child: Text(
                                      device.name,
                                      style: Theme.of(context).textTheme.titleMedium,
                                    ),
                                  ),
                                  StatusChip(
                                    status: device.connected ? HudStatus.online : HudStatus.offline,
                                    compact: true,
                                  ),
                                ],
                              ),
                            ),

                            Padding(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 16,
                              ),
                              child: Text(
                                device.subtitleParts,
                                style: context.pai.mono(size: 12),
                              ),
                            ),

                            ...group.plugins.map(
                              (plugin) => _buildOtherDeviceTile(plugin, device),
                            ),
                          ],
                        );
                      },
                    ),
                  ],
                ],
                const SizedBox(height: 24),
              ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }

  Widget _buildThisDeviceTile(PluginInfo plugin) {
    final installing = _installing.contains(plugin.id);
    final installed = widget.manager.isInstalled(plugin.id);
    final enabled = widget.manager.isEnabled(plugin.id);
    final update = installed ? widget.manager.updateFor(plugin.id) : null;
    final canUpdate = update?.updateAvailable ?? false;
    // Installed: show what THIS install can do (its version), not the newest catalog entry.
    final caps = installed
        ? (widget.manager.installedCapabilities(plugin.id) ?? plugin.capabilities)
        : plugin.capabilities;

    return _PluginRow(
      title: Text(plugin.name),
      subtitle: _versionLine(
        plugin, update, installed ? widget.manager.installedVersion(plugin.id) : null, caps),
      isThreeLine: true,
      trailing: installing
          ? const SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(),
            )
          : installed
          ? Wrap(
              // Wrap, not Row: with the Update button the actions no longer fit one line on a
              // narrow screen or at large text sizes; they flow onto a second line instead.
              alignment: WrapAlignment.end,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                if (canUpdate)
                  Padding(
                    padding: const EdgeInsets.only(right: 4),
                    child: FilledButton.tonalIcon(
                      key: Key('update-${plugin.id}'),
                      icon: const Icon(Icons.system_update_alt, size: 18),
                      label: const Text('Update'),
                      onPressed: () => _showUpdate(plugin),
                    ),
                  )
                else if (update?.blocked != null)
                  IconButton(
                    icon: const Icon(Icons.info_outline),
                    tooltip: "Why can't I update?",
                    onPressed: () => showPluginUpdateSheet(
                      context, plugin: plugin, update: update!, canUpdate: false),
                  ),
                Switch(
                  value: enabled,
                  onChanged: (value) async {
                    try {
                      if (value) {
                        await widget.manager.enable(plugin.id);
                      } else {
                        await widget.manager.disable(plugin.id);
                      }
                    } catch (e) {
                      if (!mounted) return;
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text('Operation failed: $e')),
                      );
                    }
                  },
                ),
                IconButton(
                  icon: const Icon(Icons.science_outlined),
                  tooltip: 'Test capabilities',
                  onPressed: () {
                    Navigator.of(context).push(
                      MaterialPageRoute(
                        builder: (_) => PluginTestScreen(
                          manager: widget.manager,
                          dispatcher: widget.manager.dispatcher,
                          plugin: plugin,
                          device: widget.thisDevice!,
                          thisDeviceId: widget.thisDevice!.id,
                        )
                        ,
                      ),
                    );
                  },
                ),
                IconButton(
                  icon: const Icon(Icons.settings),
                  tooltip: 'Configure capabilities',
                  onPressed: () async {
                    final changed = await Navigator.of(context).push<bool>(
                      MaterialPageRoute(
                        builder: (_) => CapabilityConfigScreen(
                          manager: widget.manager,
                          plugin: plugin,
                        ),
                      ),
                    );

                    if (changed == true) {
                      debugPrint(
                        '[PluginsScreen] config changed for ${plugin.id}',
                      );
                    }
                  },
                ),
                IconButton(
                  icon: const Icon(Icons.delete_outline),
                  tooltip: 'Uninstall',
                  onPressed: () => _uninstall(plugin.id),
                ),
              ],
            )
          : _installButton(plugin),
    );
  }

  /// Install, unless no version of the plugin can run on this device/app.
  Widget _installButton(PluginInfo plugin) {
    final why = !plugin.compatible
        ? plugin.incompatibleReason
        : appIncompatibility(plugin.runtimeSpec.minAppVersion);
    if (why == null) {
      return FilledButton(
        onPressed: () => _handleInstall(plugin.id, plugin.version),
        child: const Text('Install'),
      );
    }
    return Tooltip(
      message: why,
      triggerMode: TooltipTriggerMode.tap,
      child: FilledButton(onPressed: null, child: const Text('Unavailable')),
    );
  }

  Widget _buildOtherDeviceTile(PluginInfo plugin, DeviceInfo device) {
    final remote = widget.manager.remoteStateFor(device.id, plugin.id);

    if (remote.phase == RemotePhase.installing) {
      return _PluginRow(
        leading: const Icon(Icons.devices_other_outlined),
        title: Text(plugin.name),
        subtitle: Text(
          'Installing on ${device.name}…',
          style: TextStyle(fontSize: 12, color: context.pai.muted),
        ),
        trailing: const SizedBox(
          width: 24,
          height: 24,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
      );
    }

    if (remote.phase == RemotePhase.failed) {
      return _PluginRow(
        leading: const Icon(Icons.error_outline, color: PaiColors.red),
        title: Text(plugin.name),
        subtitle: Text(
          remote.error ?? 'Install failed',
          style: const TextStyle(fontSize: 12, color: PaiColors.red),
          maxLines: 3,
          overflow: TextOverflow.ellipsis,
        ),
        trailing: FilledButton(
          onPressed: device.connected
              ? () => _retryInstallOnRemote(plugin, device.id)
              : null,
          child: const Text('Retry'),
        ),
      );
    }

    final remoteInstalled = widget.manager.isInstalledOnDevice(
      plugin.id,
      device.id,
    );

    final remoteEnabled = widget.manager.isEnabledOnDevice(
      plugin.id,
      device.id,
    );

    final remoteUpdate = remoteInstalled
        ? widget.manager.updateFor(plugin.id, deviceId: device.id)
        : null;

    if (remoteInstalled) {
      return _PluginRow(
        leading: const Icon(Icons.devices_other_outlined),
        title: Text(plugin.name),
        subtitle: Text(
          'Installed · v${remoteUpdate?.installedVersion ?? plugin.version}'
          '${remoteUpdate?.updateAvailable == true ? " → v${remoteUpdate!.latestVersion}" : ""}'
          '${remoteEnabled ? " · enabled" : " · disabled"}',
          style: const TextStyle(fontSize: 12),
        ),
        trailing: Wrap(
          alignment: WrapAlignment.end,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            if (remoteUpdate?.updateAvailable == true)
              FilledButton.tonal(
                onPressed: device.connected
                    ? () => _showUpdate(plugin, deviceId: device.id, deviceName: device.name)
                    : null,
                child: const Text('Update'),
              ),
            Switch(
              value: remoteEnabled,
              onChanged: device.connected
                  ? (value) async {
                      try {
                        if (value) {
                          await widget.manager.enableOnDevice(
                            plugin.id,
                            device.id,
                          );
                        } else {
                          await widget.manager.disableOnDevice(
                            plugin.id,
                            device.id,
                          );
                        }
                      } catch (e) {
                        if (!mounted) return;
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text('Toggle failed: $e')),
                        );
                      }
                    }
                  : null,
            ),
            IconButton(
              icon: const Icon(Icons.science_outlined),
              tooltip: device.connected
                  ? 'Test capabilities on ${device.name}'
                  : 'Device offline',
              onPressed: device.connected
                  ? () => _openRemoteTest(plugin, device)
                  : null,
            ),
            IconButton(
              icon: const Icon(Icons.settings),
              tooltip: 'Configure capabilities',
              onPressed: () => _openConfig(plugin),
            ),
          ],
        ),
      );
    }

    return _PluginRow(
      leading: const Icon(Icons.devices_other_outlined),
      title: Text(plugin.name),
      subtitle: Text(
        'Available · ${plugin.version}',
        style: TextStyle(fontSize: 12, color: context.pai.muted),
      ),
      trailing: FilledButton(
        onPressed: device.connected
            ? () => _installOnRemote(plugin, device)
            : null,
        child: Text(device.connected ? 'Install' : 'Offline'),
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Remote helpers (unchanged)
  // ---------------------------------------------------------------------------

  Future<void> _installOnRemote(PluginInfo plugin, DeviceInfo device) async {
    try {
      await widget.manager.installOnDevice(
        plugin.id,
        plugin.version,
        device.id,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Install requested on ${device.name}')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('Install failed: $e')));
    }
  }

  Future<void> _retryInstallOnRemote(PluginInfo plugin, String deviceId) async {
    try {
      await widget.manager.retryInstallOnDevice(
        plugin.id,
        plugin.version,
        deviceId,
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('Retry failed: $e')));
    }
  }

  Future<void> _openConfig(PluginInfo plugin) async {
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) =>
            CapabilityConfigScreen(manager: widget.manager, plugin: plugin),
      ),
    );
    if (changed == true) {
      debugPrint('[PluginsScreen] config changed for ${plugin.id}');
    }
  }

  Future<void> _openRemoteTest(PluginInfo plugin, DeviceInfo device) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => PluginTestScreen(
          manager: widget.manager,
          dispatcher: widget.manager.dispatcher,
          plugin: plugin,
          device: device,
          thisDeviceId: widget.thisDevice!.id,
        ),
      ),
    );
  }
}
