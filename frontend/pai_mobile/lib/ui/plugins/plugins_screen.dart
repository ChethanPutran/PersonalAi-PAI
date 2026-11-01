import 'package:flutter/material.dart';

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
        final plugins = widget.manager.plugins;

        if (plugins.isEmpty) {
          return const Center(
            child: Padding(
              padding: EdgeInsets.all(24),
              child: Text(
                'No plugins in catalog.\nPull down to refresh.',
                textAlign: TextAlign.center,
              ),
            ),
          );
        }

        return RefreshIndicator(
          onRefresh: () async {
            // Trigger a catalog reload if your manager exposes one
            try {
              await widget.manager.loadCatalog(
                platform: 'linux',       // or the actual platform key
                architecture: 'x86_64',  // or the actual arch
              );
            } catch (e) {
              debugPrint('[PluginsScreen] refresh failed: $e');
            }
          },
          child: ListView.builder(
            physics: const AlwaysScrollableScrollPhysics(),
            itemCount: plugins.length,
            itemBuilder: (context, index) {
              final plugin = plugins[index];
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
                                onChanged: (v) async {
                                  try {
                                    if (v) {
                                      await widget.manager.enable(plugin.id);
                                    } else {
                                      await widget.manager.disable(plugin.id);
                                    }
                                    // ListenableBuilder already rebuilds;
                                    // no setState needed here.
                                  } catch (e) {
                                    if (!mounted) return;
                                    ScaffoldMessenger.of(context)
                                        .showSnackBar(SnackBar(
                                      content: Text('Operation failed: $e'),
                                    ));
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
                            onPressed: () =>
                                _install(plugin.id, plugin.version),
                            child: const Text('Install'),
                          ),
              );
            },
          ),
        );
      },
    ),
  );
}
}