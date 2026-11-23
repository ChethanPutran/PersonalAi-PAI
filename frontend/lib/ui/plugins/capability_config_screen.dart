
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../plugins/models/plugin_capability.dart';
import '../../plugins/models/plugin_info.dart';
import '../../plugins/plugin_manager.dart';

class CapabilityConfigScreen extends StatefulWidget {
  final PluginManager manager;
  final PluginInfo plugin;

  const CapabilityConfigScreen({
    super.key,
    required this.manager,
    required this.plugin,
  });

  @override
  State<CapabilityConfigScreen> createState() =>
      _CapabilityConfigScreenState();
}

class _CapabilityConfigScreenState extends State<CapabilityConfigScreen> {
  /// capabilityId -> { enabled: bool, params: Map<String, dynamic> }
  final Map<String, Map<String, dynamic>> _caps = {};

  /// Parameter controllers, indexed by capabilityId.parameterName.
  final Map<String, TextEditingController> _controllers = {};

  List<PluginCapability> _capabilities = [];

  bool _loading = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final controller in _controllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  // ------------------------------------------------------------------
  // Capability definitions
  // ------------------------------------------------------------------

  List<PluginCapability> _loadCapabilityDefinitions() {
    final devicePlugin =
        widget.manager.service.registry.byId(widget.plugin.id);

    if (devicePlugin == null) {
      throw StateError(
        'Plugin ${widget.plugin.id} is not installed '
        'or is missing from the local registry.',
      );
    }

    return devicePlugin.capabilities;
  }

  // ------------------------------------------------------------------
  // Load configuration
  // ------------------------------------------------------------------

  Future<void> _load() async {
    if (mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }

    try {
      final capabilities = _loadCapabilityDefinitions();

      final persisted = await widget.manager.loadCapabilityConfig(
        widget.plugin.id,
      );

      _caps.clear();
      _disposeControllers();

      for (final capability in capabilities) {
        final saved = persisted[capability.id];

        final savedConfig = saved is Map
          ? Map<String, dynamic>.from(saved as Map<dynamic, dynamic>)
            : <String, dynamic>{};

        final savedParams = savedConfig['params'] is Map
            ? Map<String, dynamic>.from(savedConfig['params'])
            : <String, dynamic>{};

        final params = <String, dynamic>{};

        // Initialize values from the capability property schema.
        for (final entry in capability.properties.entries) {
          final schema = entry.value;

          dynamic defaultValue;

          if (schema is Map) {
            defaultValue = schema['default'];

            // Initialize empty object/array parameters when no default
            // has been declared.
            if (defaultValue == null) {
              final type = schema['type']?.toString();

              if (type == 'object') {
                defaultValue = <String, dynamic>{};
              } else if (type == 'array') {
                defaultValue = <dynamic>[];
              }
            }
          }

          params[entry.key] = savedParams.containsKey(entry.key)
              ? savedParams[entry.key]
              : defaultValue;
        }

        _caps[capability.id] = {
          'enabled': savedConfig['enabled'] as bool? ?? true,
          'params': params,
        };
      }

      _capabilities = capabilities;

      _initializeControllers();
    } catch (e, stackTrace) {
      debugPrint('[CapabilityConfig] Load failed: $e');
      debugPrintStack(stackTrace: stackTrace);

      _error = 'Failed to load configuration: $e';
    } finally {
      if (mounted) {
        setState(() {
          _loading = false;
        });
      }
    }
  }

  // ------------------------------------------------------------------
  // Text controllers
  // ------------------------------------------------------------------

  String _controllerKey(String capabilityId, String parameter) {
    return '$capabilityId::$parameter';
  }

  void _initializeControllers() {
    for (final capability in _capabilities) {
      final config = _caps[capability.id];
      if (config == null) continue;

      final params = config['params'] as Map<String, dynamic>;

      for (final entry in capability.properties.entries) {
        final schema = entry.value;

        if (schema is! Map) continue;

        final type = schema['type']?.toString() ?? 'string';

        if (type == 'boolean' || (schema['enum'] is List)) {
          continue;
        }

        final key = _controllerKey(capability.id, entry.key);

        _controllers[key] = TextEditingController(
          text: params[entry.key]?.toString() ?? '',
        );
      }
    }
  }

  void _disposeControllers() {
    for (final controller in _controllers.values) {
      controller.dispose();
    }

    _controllers.clear();
  }

  // ------------------------------------------------------------------
  // Save configuration
  // ------------------------------------------------------------------

  Future<void> _save() async {
    if (_saving || _loading || _error != null) return;

    setState(() {
      _saving = true;
    });

    try {
      // Save a snapshot instead of passing the mutable UI state directly.
      final config = <String, Map<String, dynamic>>{};

      for (final entry in _caps.entries) {
        final current = entry.value;

        config[entry.key] = {
          'enabled': current['enabled'] == true,
          'params': Map<String, dynamic>.from(
            current['params'] as Map<String, dynamic>,
          ),
        };
      }

      await widget.manager.saveCapabilityConfig(
        widget.plugin.id,
        config,
      );

      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Configuration saved'),
        ),
      );

      Navigator.of(context).pop(true);
    } catch (e, stackTrace) {
      debugPrint('[CapabilityConfig] Save failed: $e');
      debugPrintStack(stackTrace: stackTrace);

      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Save failed: $e'),
        ),
      );
    } finally {
      if (mounted) {
        setState(() {
          _saving = false;
        });
      }
    }
  }

  // ------------------------------------------------------------------
  // Capability card
  // ------------------------------------------------------------------

  Widget _buildCard(PluginCapability capability) {
    final config = _caps[capability.id];

    if (config == null) {
      return const SizedBox.shrink();
    }

    final enabled = config['enabled'] == true;
    final params = config['params'] as Map<String, dynamic>;

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ExpansionTile(
        key: PageStorageKey('capability-${capability.id}'),
        initiallyExpanded: enabled,
        title: Text(
          capability.id,
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        subtitle: capability.description.isEmpty
            ? null
            : Text(capability.description),
        trailing: Switch(
          value: enabled,
          onChanged: _saving
              ? null
              : (value) {
                  setState(() {
                    config['enabled'] = value;
                  });
                },
        ),
        children: [
          if (capability.properties.isEmpty)
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text(
                'No parameters',
                style: TextStyle(color: Colors.grey),
              ),
            )
          else
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: capability.properties.entries.map((entry) {
                  if (entry.value is! Map) {
                    return const SizedBox.shrink();
                  }

                  return _buildParamField(
                    capabilityId: capability.id,
                    name: entry.key,
                    schema: Map<String, dynamic>.from(entry.value as Map),
                    value: params[entry.key],
                    enabled: enabled && !_saving,
                    onChanged: (value) {
                      setState(() {
                        params[entry.key] = value;
                      });
                    },
                  );
                }).toList(),
              ),
            ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------------
  // Parameter field
  // ------------------------------------------------------------------

  Widget _buildParamField({
    required String capabilityId,
    required String name,
    required Map<String, dynamic> schema,
    required dynamic value,
    required bool enabled,
    required ValueChanged<dynamic> onChanged,
  }) {
    final type = schema['type']?.toString() ?? 'string';
    final description = schema['description']?.toString() ?? '';
    final enumValues = schema['enum'] as List<dynamic>?;
    final defaultValue = schema['default'];

    final controller = _controllers[
        _controllerKey(capabilityId, name)];

    Widget field;

    if (enumValues != null && enumValues.isNotEmpty) {
      final selectedValue = enumValues.contains(value)
          ? value
          : enumValues.first;

      field = DropdownButtonFormField<dynamic>(
        value: selectedValue,
        isExpanded: true,
        items: enumValues.map((item) {
          return DropdownMenuItem<dynamic>(
            value: item,
            child: Text(item.toString()),
          );
        }).toList(),
        onChanged: enabled
            ? (newValue) => onChanged(newValue)
            : null,
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    } else if (type == 'boolean') {
      field = SwitchListTile(
        contentPadding: EdgeInsets.zero,
        value: value == true,
        onChanged: enabled ? onChanged : null,
        title: const Text('Enabled'),
      );
    } else if (type == 'integer') {
      field = TextFormField(
        key: ValueKey('$capabilityId.$name.integer'),
        controller: controller,
        enabled: enabled,
        keyboardType: const TextInputType.numberWithOptions(
          signed: true,
        ),
        inputFormatters: [
          FilteringTextInputFormatter.allow(RegExp(r'^-?\d*')),
        ],
        onChanged: (text) {
          onChanged(text.isEmpty ? null : int.tryParse(text));
        },
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    } else if (type == 'number') {
      field = TextFormField(
        key: ValueKey('$capabilityId.$name.number'),
        controller: controller,
        enabled: enabled,
        keyboardType: const TextInputType.numberWithOptions(
          signed: true,
          decimal: true,
        ),
        inputFormatters: [
          FilteringTextInputFormatter.allow(
            RegExp(r'^-?\d*\.?\d*'),
          ),
        ],
        onChanged: (text) {
          onChanged(text.isEmpty ? null : double.tryParse(text));
        },
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    } else if (type == 'object' || type == 'array') {
      field = TextFormField(
        key: ValueKey('$capabilityId.$name.$type'),
        controller: controller,
        enabled: enabled,
        minLines: 2,
        maxLines: 5,
        onChanged: (text) {
          try {
            final parsed = jsonDecode(text);

            if (type == 'object' && parsed is Map) {
              onChanged(Map<String, dynamic>.from(parsed));
            } else if (type == 'array' && parsed is List) {
              onChanged(parsed);
            }
          } catch (_) {
            // Keep the last valid JSON value until the input is valid.
          }
        },
        decoration: InputDecoration(
          border: const OutlineInputBorder(),
          isDense: true,
          hintText: type == 'object' ? '{"key": "value"}' : '["item"]',
        ),
      );
    } else {
      field = TextFormField(
        key: ValueKey('$capabilityId.$name.string'),
        controller: controller,
        enabled: enabled,
        onChanged: onChanged,
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  name,
                  style: const TextStyle(
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
              if (defaultValue != null)
                Text(
                  'Default: $defaultValue',
                  style: TextStyle(
                    fontSize: 12,
                    color: Colors.grey.shade600,
                    fontStyle: FontStyle.italic,
                  ),
                ),
            ],
          ),
          if (description.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2, bottom: 4),
              child: Text(
                description,
                style: TextStyle(
                  fontSize: 12,
                  color: Colors.grey.shade600,
                ),
              ),
            ),
          field,
        ],
      ),
    );
  }

  // ------------------------------------------------------------------
  // Build
  // ------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Configure: ${widget.plugin.name}'),
        actions: [
          if (!_loading && _error == null)
            IconButton(
              tooltip: 'Save',
              icon: _saving
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                      ),
                    )
                  : const Icon(Icons.save),
              onPressed: _saving ? null : _save,
            ),
        ],
      ),
      body: _loading
          ? const Center(
              child: CircularProgressIndicator(),
            )
          : _error != null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(
                      _error!,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: Colors.redAccent,
                      ),
                    ),
                  ),
                )
              : _capabilities.isEmpty
                  ? const Center(
                      child: Text('No capabilities to configure'),
                    )
                  : ListView(
                      padding: const EdgeInsets.all(16),
                      children: _capabilities
                          .map(_buildCard)
                          .toList(),
                    ),
    );
  }
}
