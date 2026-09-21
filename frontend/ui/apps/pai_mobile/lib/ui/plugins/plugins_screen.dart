import 'package:flutter/material.dart';

import '../../../plugins/models/plugin_info.dart';
import '../../../plugins/plugin_manager.dart';


class PluginsScreen extends StatefulWidget {
  final PluginManager manager;

  const PluginsScreen({
    super.key,
    required this.manager,
  });


  @override
  State<PluginsScreen> createState() =>
      _PluginsScreenState();
}


class _PluginsScreenState extends State<PluginsScreen> {
  final Set<String> _installing = {};

  Future<void> _install(String pluginId) async {
    setState(() {
      _installing.add(pluginId);
    });

    try {
      await widget.manager.install(pluginId);

      if (!mounted) return;

      setState(() {});
      
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Plugin installed'),
        ),
      );
    } catch (e) {
      if (!mounted) return;

      setState(() {});

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Installation failed: $e'),
        ),
      );
    } finally {
      if (mounted) {
        setState(() {
          _installing.remove(pluginId);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final plugins = widget.manager.plugins;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Plugins'),
      ),
      body: ListView.builder(
        itemCount: plugins.length,
        itemBuilder: (context, index) {
          final plugin = plugins[index];

          final installing =
              _installing.contains(plugin.id);

          final installed =
              plugin.state != PluginState.available;

          final enabled =
              plugin.state == PluginState.enabled;

          return ListTile(
            title: Text(plugin.name),

            subtitle: Text(
              '${plugin.version}\n'
              '${plugin.capabilities.join(", ")}',
            ),

            isThreeLine: true,

            trailing: installing
                ? const SizedBox(
                    width: 24,
                    height: 24,
                    child: CircularProgressIndicator(),
                  )
                : installed
                    ? Switch(
                        value: enabled,
                        onChanged: (value) async {
                          try {
                            if (value) {
                              await widget.manager.enable(
                                plugin.id,
                              );
                            } else {
                              await widget.manager.disable(
                                plugin.id,
                              );
                            }

                            if (mounted) {
                              setState(() {});
                            }
                          } catch (e) {
                            if (!mounted) return;

                            ScaffoldMessenger.of(context)
                                .showSnackBar(
                              SnackBar(
                                content: Text(
                                  'Plugin operation failed: $e',
                                ),
                              ),
                            );
                          }
                        },
                      )
                    : ElevatedButton(
                        onPressed: () {
                          _install(plugin.id);
                        },
                        child: const Text('Install'),
                      ),
          );
        },
      ),
    );
  }
}

