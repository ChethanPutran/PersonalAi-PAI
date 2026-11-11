import 'package:flutter/material.dart';
import 'package:pai_mobile/plugins/models/catalog_response.dart';

import '../../plugins/models/plugin_info.dart';
import '../../plugins/plugin_manager.dart';
import 'capability_config_screen.dart';
import 'plugin_test_screen.dart';

class PluginsScreen extends StatefulWidget {
  final PluginManager manager;
  const PluginsScreen({super.key, required this.manager});

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
          Text(title,
              style: Theme.of(context).textTheme.titleMedium),
          if (subtitle != null)
            Text(subtitle!,
                style: TextStyle(
                  fontSize: 11, color: Colors.grey[600],
                )),
        ],
      ),
    );
  }
}

class _PluginsScreenState extends State<PluginsScreen> {
  final Set<String> _installing = {};

  Future<void> _install(String id, String version) async {
    setState(() => _installing.add(id));
    try {
      await widget.manager.install(id, version);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Plugin installed')));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Install failed: $e')));
    } finally {
      if (mounted) setState(() => _installing.remove(id));
    }
  }

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
    messenger.showSnackBar(
      SnackBar(content: Text('Uninstall failed: $e')),
    );
  }
}

@override
Widget build(BuildContext context) {
  return Scaffold(
    appBar: AppBar(title: const Text('Plugins')),
    body: ListenableBuilder(
      listenable: widget.manager,
      builder: (context, _) {
        final thisDevicePlugins = widget.manager.thisDevicePlugins;
        final otherGroups = widget.manager.otherDeviceGroups;
        final thisDevice = widget.manager.thisDevice;

        if (thisDevice == null) {
          return const Center(child: Text('Loading…'));
        }

        if (thisDevicePlugins.isEmpty && otherGroups.isEmpty) {
          return RefreshIndicator(
            onRefresh: widget.manager.loadCatalog,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: const [
                SizedBox(height: 200),
                Center(child: Text('No plugins compatible with your devices.')),
              ],
            ),
          );
        }

        return RefreshIndicator(
          onRefresh: widget.manager.loadCatalog,
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            children: [
              // ---------- Section 1: this device ----------
              _SectionHeader(
                title: 'This device',
                subtitle:
                    '${thisDevice.label} · ${thisDevice.platform}/${thisDevice.architecture}',
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

              // ---------- Section 2: other devices ----------
              if (otherGroups.isNotEmpty) ...[
                const SizedBox(height: 16),
                const _SectionHeader(title: 'Your other devices'),
                for (final group in otherGroups) ...[
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            group.device.label,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                        ),
                        if (!group.device.connected)
                          const Chip(
                            label: Text('offline', style: TextStyle(fontSize: 10)),
                            visualDensity: VisualDensity.compact,
                          ),
                      ],
                    ),
                  ),
                  ...group.plugins.map((p) => _buildOtherDeviceTile(p, group.device)),
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
  final installed = plugin.isInstalled;
  final enabled = plugin.isEnabled;

  return ListTile(
    title: Text(plugin.name),
    subtitle: Text(
      '${plugin.version}\n${plugin.capabilities.join(", ")}',
    ),
    isThreeLine: true,
    trailing: installing
        ? const SizedBox(
            width: 24, height: 24,
            child: CircularProgressIndicator(),
          )
        : installed
            ? Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Switch(
                    value: enabled,
                    onChanged: (v) async {
                      try {
                        if (v) {
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
                                        installer:
                                            widget.manager.service.installer,
                                        runtime:
                                            widget.manager.service.runtime,
                                      ),
                                    ),
                                  );
                                },
                              ),
                  IconButton(
                                icon: const Icon(Icons.settings),
                                tooltip: 'Configure capabilities',
                                onPressed: () async {
                                  final changed =
                                      await Navigator.of(context).push<bool>(
                                    MaterialPageRoute(
                                      builder: (_) => CapabilityConfigScreen(
                                        manager: widget.manager,
                                        plugin: plugin,
                                      ),
                                    ),
                                  );
                                  // No setState — ListenableBuilder handles it.
                                  if (changed == true) {
                                    debugPrint(
                                        '[PluginsScreen] config changed');
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
                onPressed: () => _install(plugin.id, plugin.version),
                child: const Text('Install'),
              ),
  );
}

Widget _buildOtherDeviceTile(PluginInfo plugin, DeviceDescriptor device) {
  final remote = widget.manager.remoteStateFor(device.deviceId, plugin.id);
  final online = device.connected;

  // ---- 1. Installing: spinner, no action ----
  if (remote.phase == RemotePhase.installing) {
    return ListTile(
      leading: const Icon(Icons.devices_other_outlined),
      title: Text(plugin.name),
      subtitle: Text(
        'Installing on ${device.label}…',
        style: TextStyle(fontSize: 12, color: Colors.grey[600]),
      ),
      trailing: const SizedBox(
        width: 24,
        height: 24,
        child: CircularProgressIndicator(strokeWidth: 2),
      ),
    );
  }

  // ---- 2. Failed: error + retry ----
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
        onPressed: online
            ? () => _retryInstallOnRemote(plugin, device)
            : null,
        child: const Text('Retry', style: TextStyle(fontSize: 12)),
      ),
    );
  }

  // ---- 3. Installed: switch + test + configure ----
  if (plugin.isInstalled) {
    final enabled = plugin.isEnabled;
    return ListTile(
      leading: const Icon(Icons.devices_other_outlined),
      title: Text(plugin.name),
      subtitle: Text(
        'Installed · ${plugin.version}${enabled ? " · enabled" : ""}',
        style: const TextStyle(fontSize: 12),
      ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Switch(
            value: enabled,
            onChanged: online
                ? (v) async {
                    try {
                      if (v) {
                        await widget.manager
                            .enableOnDevice(plugin.id, device.deviceId);
                      } else {
                        await widget.manager
                            .disableOnDevice(plugin.id, device.deviceId);
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
            tooltip: online
                ? 'Test capabilities on ${device.label}'
                : 'Device offline',
            onPressed: online
                ? () => _openRemoteTest(plugin, device)
                : null,
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Configure capabilities',
            // Config is stored per-user, so it works regardless of
            // which device the plugin is installed on.
            onPressed: () => _openConfig(plugin),
          ),
        ],
      ),
    );
  }

  // ---- 4. Available: install button ----
  return ListTile(
    leading: const Icon(Icons.devices_other_outlined),
    title: Text(plugin.name),
    subtitle: Text(
      'Available · ${plugin.version}',
      style: TextStyle(fontSize: 12, color: Colors.grey[600]),
    ),
    trailing: ElevatedButton(
      onPressed: online ? () => _installOnRemote(plugin, device) : null,
      child: Text(
        online ? 'Install' : 'Offline',
        style: const TextStyle(fontSize: 12),
      ),
    ),
  );
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

Future<void> _installOnRemote(PluginInfo plugin, DeviceDescriptor device) async {
  try {
    await widget.manager.installOnDevice(
      plugin.id,
      plugin.version,
      device.deviceId,
    );
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Install requested on ${device.label}')),
    );
    // Tile state is driven by the manager's remote-op tracker — the
    // ListenableBuilder rebuilds on `notifyListeners()` calls from
    // installOnDevice / the poll.
  } catch (e) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Install failed: $e')),
    );
    // Error is also recorded in the tracker; the tile shows it.
  }
}

Future<void> _retryInstallOnRemote(
  PluginInfo plugin,
  DeviceDescriptor device,
) async {
  try {
    await widget.manager.retryInstallOnDevice(
      plugin.id,
      plugin.version,
      device.deviceId,
    );
  } catch (e) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Retry failed: $e')),
    );
  }
}

Future<void> _openConfig(PluginInfo plugin) async {
  final changed = await Navigator.of(context).push<bool>(
    MaterialPageRoute(
      builder: (_) => CapabilityConfigScreen(
        manager: widget.manager,
        plugin: plugin,
      ),
    ),
  );
  if (changed == true) {
    debugPrint('[PluginsScreen] config changed for ${plugin.id}');
  }
}

Future<void> _openRemoteTest(PluginInfo plugin, DeviceDescriptor device) async {
  await Navigator.of(context).push(
    MaterialPageRoute(
      builder: (_) => PluginTestScreen(
        manager: widget.manager,
        plugin: plugin,
        device: device,
      ),
    ),
  );
}
}