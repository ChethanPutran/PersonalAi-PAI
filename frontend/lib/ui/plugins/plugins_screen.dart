import 'package:flutter/material.dart';
import 'package:pai_mobile/device/repositories/device_repository.dart';
import 'package:pai_mobile/models/device.dart';
import 'package:pai_mobile/plugins/plugin_repository.dart';
import 'package:pai_mobile/plugins/plugin_service.dart';
import '../../plugins/models/plugin_info.dart';
import '../../plugins/plugin_manager.dart';
import 'capability_config_screen.dart';
import 'plugin_test_screen.dart';

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
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          if (subtitle != null)
            Text(
              subtitle!,
              style: TextStyle(fontSize: 11, color: Colors.grey[600]),
            ),
        ],
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
    return Scaffold(
      appBar: AppBar(title: const Text('Plugins')),
      body: ListenableBuilder(
        listenable: widget.manager,
        builder: (context, _) {
          final thisDevicePlugins = widget.manager.thisDevicePlugins;
          final otherGroups = widget.manager.otherDeviceGroups;

          if (thisDevicePlugins.isEmpty && otherGroups.isEmpty) {
            return RefreshIndicator(
              onRefresh: widget.manager.loadCatalog,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: const [
                  SizedBox(height: 200),
                  Center(
                    child: Text('No plugins compatible with your devices.'),
                  ),
                ],
              ),
            );
          }

          return RefreshIndicator(
            onRefresh: widget.manager.loadCatalog,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: [
                _SectionHeader(
                  title: 'This device',
                  subtitle:
                      '${widget.thisDevice?.name} · ${widget.thisDevice?.platform}/${widget.thisDevice?.architecture}',
                ),
                if (thisDevicePlugins.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                    child: Text(
                      'No plugins available for this device.',
                      style: TextStyle(color: Colors.grey),
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
                                      style: const TextStyle(
                                        fontWeight: FontWeight.w600,
                                      ),
                                    ),
                                  ),
                                  Chip(
                                    label: Text(
                                      device.connected ? 'Online' : 'Offline',
                                      style: const TextStyle(fontSize: 10),
                                    ),
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
                                style: TextStyle(
                                  fontSize: 12,
                                  color: Colors.grey[600],
                                ),
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
          );
        },
      ),
    );
  }

  Widget _buildThisDeviceTile(PluginInfo plugin) {
    final installing = _installing.contains(plugin.id);
    final installed = widget.manager.isInstalled(plugin.id);
    final enabled = widget.manager.isEnabled(plugin.id);

    return ListTile(
      title: Text(plugin.name),
      subtitle: Text('${plugin.version}\n${plugin.capabilities.join(", ")}'),
      isThreeLine: true,
      trailing: installing
          ? const SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(),
            )
          : installed
          ? Row(
              mainAxisSize: MainAxisSize.min,
              children: [
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
          : ElevatedButton(
              onPressed: () => _handleInstall(plugin.id, plugin.version),
              child: const Text('Install'),
            ),
    );
  }

  Widget _buildOtherDeviceTile(PluginInfo plugin, DeviceInfo device) {
    final remote = widget.manager.remoteStateFor(device.id, plugin.id);

    if (remote.phase == RemotePhase.installing) {
      return ListTile(
        leading: const Icon(Icons.devices_other_outlined),
        title: Text(plugin.name),
        subtitle: Text(
          'Installing on ${device.name}…',
          style: TextStyle(fontSize: 12, color: Colors.grey[600]),
        ),
        trailing: const SizedBox(
          width: 24,
          height: 24,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
      );
    }

    if (remote.phase == RemotePhase.failed) {
      return ListTile(
        leading: const Icon(Icons.error_outline, color: Colors.red),
        title: Text(plugin.name),
        subtitle: Text(
          remote.error ?? 'Install failed',
          style: const TextStyle(fontSize: 12, color: Colors.red),
          maxLines: 3,
          overflow: TextOverflow.ellipsis,
        ),
        trailing: ElevatedButton(
          onPressed: device.connected
              ? () => _retryInstallOnRemote(plugin, device.id)
              : null,
          child: const Text('Retry', style: TextStyle(fontSize: 12)),
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

    if (remoteInstalled) {
      return ListTile(
        leading: const Icon(Icons.devices_other_outlined),
        title: Text(plugin.name),
        subtitle: Text(
          'Installed · ${plugin.version}'
          '${remoteEnabled ? " · enabled" : " · disabled"}',
          style: const TextStyle(fontSize: 12),
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
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

    return ListTile(
      leading: const Icon(Icons.devices_other_outlined),
      title: Text(plugin.name),
      subtitle: Text(
        'Available · ${plugin.version}',
        style: TextStyle(fontSize: 12, color: Colors.grey[600]),
      ),
      trailing: ElevatedButton(
        onPressed: device.connected
            ? () => _installOnRemote(plugin, device)
            : null,
        child: Text(
          device.connected ? 'Install' : 'Offline',
          style: const TextStyle(fontSize: 12),
        ),
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
