import 'package:flutter/material.dart';

import '../../plugins/models/plugin_info.dart';
import '../../plugins/plugin_manager.dart';

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
    try {
      await widget.manager.uninstall(id);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Plugin uninstalled')));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Uninstall failed: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final plugins = widget.manager.plugins;
    return Scaffold(
      appBar: AppBar(title: const Text('Plugins')),
      body: ListView.builder(
        itemCount: plugins.length,
        itemBuilder: (context, index) {
          final plugin = plugins[index];
          final installing = _installing.contains(plugin.id);
          final installed = plugin.isInstalled;
          final enabled = plugin.isEnabled;

          return ListTile(
            title: Text(plugin.name),
            subtitle: Text('${plugin.version}\n${plugin.capabilities.join(", ")}'),
            isThreeLine: true,
            trailing: installing
                ? const SizedBox(
                    width: 24, height: 24,
                    child: CircularProgressIndicator(),
                  )
                : installed
                    ? Row(mainAxisSize: MainAxisSize.min, children: [
                        Switch(
                          value: enabled,
                          onChanged: (v) async {
                            try {
                              if (v) {
                                await widget.manager.enable(plugin.id);
                              } else {
                                await widget.manager.disable(plugin.id);
                              }
                              if (mounted) setState(() {});
                            } catch (e) {
                              if (!mounted) return;
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(content: Text('Operation failed: $e')),
                              );
                            }
                          },
                        ),
                        IconButton(
                          icon: const Icon(Icons.delete_outline),
                          tooltip: 'Uninstall',
                          onPressed: () => _uninstall(plugin.id),
                        ),
                      ])
                    : ElevatedButton(
                        onPressed: () => _install(plugin.id, plugin.version),
                        child: const Text('Install'),
                      ),
          );
        },
      ),
    );
  }
}