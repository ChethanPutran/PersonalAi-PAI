import 'dart:convert';
import 'package:flutter/material.dart';
import '../../services/plugin_service.dart';

class PluginDetailScreen extends StatefulWidget {
  final String pluginId;

  const PluginDetailScreen({required this.pluginId, super.key});

  @override
  State<PluginDetailScreen> createState() => _PluginDetailScreenState();
}

class _PluginDetailScreenState extends State<PluginDetailScreen> {
  Map<String, dynamic>? _details;
  bool _loading = true;
  String? _error;
  Map<String, dynamic> _configValues = {};
  Map<String, dynamic>? _configSchema;
  bool _saving = false;

  // For generic (no‑schema) key‑value editor
  final TextEditingController _newKeyController = TextEditingController();
  final TextEditingController _newValueController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _newKeyController.dispose();
    _newValueController.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final d = await PluginService.getPlugin(widget.pluginId);
      _details = d;
      _configSchema = Map<String, dynamic>.from(d['config_schema'] ?? {});
      final savedConfig = Map<String, dynamic>.from(d['config'] ?? {});

      // Initialize _configValues with saved config, falling back to schema defaults
      _configValues = {};
      if (_configSchema != null && _configSchema!.isNotEmpty) {
        _configSchema!.forEach((key, schema) {
          if (savedConfig.containsKey(key)) {
            _configValues[key] = savedConfig[key];
          } else {
            _configValues[key] = schema['default'];
          }
        });
      } else {
        // No schema → use saved config as‑is (or empty)
        _configValues = savedConfig;
      }
    } catch (e) {
      _error = 'Failed to fetch details: $e';
    } finally {
      if (mounted) {
        setState(() {
          _loading = false;
        });
      }
    }
  }

  // --- Generic (no‑schema) value management ---

  void _updateConfigValue(String key, dynamic value) {
    setState(() {
      _configValues[key] = value;
    });
  }

  void _removeConfigKey(String key) {
    setState(() {
      _configValues.remove(key);
    });
  }

  void _addNewKeyValue() {
    final key = _newKeyController.text.trim();
    final value = _newValueController.text.trim();
    if (key.isNotEmpty) {
      dynamic parsedValue = value;
      try {
        parsedValue = jsonDecode(value);
      } catch (_) {}
      setState(() {
        _configValues[key] = parsedValue;
        _newKeyController.clear();
        _newValueController.clear();
      });
    }
  }

  // --- Save ---

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final resp = await PluginService.setPluginConfig(widget.pluginId, _configValues);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Saved: ${resp['status'] ?? 'ok'}')),
      );
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Save failed: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  // --- Schema‑driven form building ---

  Widget _buildSchemaForm() {
    final children = <Widget>[];
    _configSchema!.forEach((key, schema) {
      final type = schema['type'] as String? ?? 'string';
      final description = schema['description'] as String? ?? '';
      final currentValue = _configValues[key];
      final enumValues = schema['enum'] as List<dynamic>?;

      Widget input;

      // Build input based on type and enum
      if (enumValues != null && enumValues.isNotEmpty) {
        // Dropdown for enum
        input = DropdownButtonFormField<dynamic>(
          value: currentValue ?? enumValues.first,
          items: enumValues.map((v) {
            return DropdownMenuItem(value: v, child: Text(v.toString()));
          }).toList(),
          onChanged: (v) => _updateConfigValue(key, v),
          decoration: const InputDecoration(
            border: OutlineInputBorder(),
            isDense: true,
          ),
        );
      } else if (type == 'boolean') {
        input = Switch(
          value: currentValue ?? false,
          onChanged: (v) => _updateConfigValue(key, v),
        );
      } else if (type == 'integer' || type == 'number') {
        input = TextFormField(
          initialValue: currentValue?.toString() ?? '',
          keyboardType: TextInputType.numberWithOptions(decimal: type == 'number'),
          onChanged: (v) {
            if (type == 'integer') {
              _updateConfigValue(key, int.tryParse(v));
            } else {
              _updateConfigValue(key, double.tryParse(v));
            }
          },
          decoration: const InputDecoration(
            border: OutlineInputBorder(),
            isDense: true,
          ),
        );
      } else {
        // Default: string / text
        input = TextFormField(
          initialValue: currentValue?.toString() ?? '',
          onChanged: (v) => _updateConfigValue(key, v),
          decoration: const InputDecoration(
            border: OutlineInputBorder(),
            isDense: true,
          ),
        );
      }

      children.add(
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 8.0),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                flex: 2,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      key,
                      style: const TextStyle(fontWeight: FontWeight.w500),
                    ),
                    if (description.isNotEmpty)
                      Text(
                        description,
                        style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                      ),
                  ],
                ),
              ),
              Expanded(
                flex: 3,
                child: input,
              ),
            ],
          ),
        ),
      );
    });

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: children,
    );
  }

  // --- Generic (no‑schema) form building ---

  Widget _buildGenericForm() {
    return Column(
      children: [
        // Existing entries
        ..._configValues.entries.map(
          (e) => _buildGenericRow(e.key, e.value),
        ),
        const SizedBox(height: 8),
        // Add new key‑value row
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _newKeyController,
                decoration: const InputDecoration(
                  hintText: 'New key',
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                controller: _newValueController,
                decoration: const InputDecoration(
                  hintText: 'Value (JSON)',
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
              ),
            ),
            IconButton(
              icon: const Icon(Icons.add_circle),
              onPressed: _addNewKeyValue,
              tooltip: 'Add key‑value',
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildGenericRow(String key, dynamic value) {
    Widget input;
    if (value is bool) {
      input = Switch(
        value: value,
        onChanged: (v) => _updateConfigValue(key, v),
      );
    } else if (value is int) {
      input = TextFormField(
        initialValue: value.toString(),
        keyboardType: TextInputType.number,
        onChanged: (v) => _updateConfigValue(key, int.tryParse(v) ?? value),
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    } else if (value is double) {
      input = TextFormField(
        initialValue: value.toString(),
        keyboardType: TextInputType.numberWithOptions(decimal: true),
        onChanged: (v) => _updateConfigValue(key, double.tryParse(v) ?? value),
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    } else {
      input = TextFormField(
        initialValue: value.toString(),
        onChanged: (v) => _updateConfigValue(key, v),
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4.0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            flex: 2,
            child: Text(key, style: const TextStyle(fontWeight: FontWeight.w500)),
          ),
          Expanded(
            flex: 3,
            child: input,
          ),
          IconButton(
            icon: const Icon(Icons.delete, color: Colors.red),
            onPressed: () => _removeConfigKey(key),
            tooltip: 'Remove this key',
          ),
        ],
      ),
    );
  }

  // --- Main build ---

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Plugin: ${widget.pluginId}'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _load,
            tooltip: 'Reload details',
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        _details?['name']?.toString() ?? widget.pluginId,
                        style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 8),
                      Text(_details?['description']?.toString() ?? ''),
                      const SizedBox(height: 16),
                      const Text(
                        'Configuration',
                        style: TextStyle(fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 8),
                      Expanded(
                        child: SingleChildScrollView(
                          child: Column(
                            children: [
                              // Use schema if available, else generic
                              if (_configSchema != null && _configSchema!.isNotEmpty)
                                _buildSchemaForm()
                              else
                                _buildGenericForm(),
                              const SizedBox(height: 12),
                              // JSON preview
                              Container(
                                padding: const EdgeInsets.all(8),
                                decoration: BoxDecoration(
                                  color: Colors.grey.shade200,
                                  borderRadius: BorderRadius.circular(8),
                                ),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    const Text(
                                      'Generated JSON (read‑only)',
                                      style: TextStyle(fontWeight: FontWeight.w600),
                                    ),
                                    const SizedBox(height: 4),
                                    SelectableText(
                                      const JsonEncoder.withIndent('  ').convert(_configValues),
                                      style: const TextStyle(fontSize: 12, fontFamily: 'monospace'),
                                    ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: ElevatedButton.icon(
                              onPressed: _saving ? null : _save,
                              icon: const Icon(Icons.save),
                              label: Text(_saving ? 'Saving...' : 'Save'),
                            ),
                          ),
                          const SizedBox(width: 12),
                          OutlinedButton(
                            onPressed: () => Navigator.of(context).pop(),
                            child: const Text('Close'),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
    );
  }
}