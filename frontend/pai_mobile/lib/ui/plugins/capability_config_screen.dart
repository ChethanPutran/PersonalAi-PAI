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
  State<CapabilityConfigScreen> createState() => _CapabilityConfigScreenState();
}

class _CapabilityConfigScreenState extends State<CapabilityConfigScreen> {
  /// capabilityId -> { 'enabled': bool, 'params': Map<String,dynamic> }
  final Map<String, Map<String, dynamic>> _caps = {};

  bool _loading = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final persisted = await widget.manager.loadCapabilityConfig(widget.plugin.id);

      for (final cap in widget.plugin.capabilityDetails) {
        final saved = persisted[cap.id] ?? {};
        _caps[cap.id] = {
          'enabled': saved['enabled'] as bool? ?? true, // default ON
          'params': {
            ...cap.defaults,
            ...(saved['params'] is Map
                ? Map<String, dynamic>.from(saved['params'])
                : const <String, dynamic>{}),
          },
        };
      }
    } catch (e) {
      _error = 'Failed to load config: $e';
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      await widget.manager.saveCapabilityConfig(widget.plugin.id, _caps);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Configuration saved')),
      );
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Save failed: $e')),
      );
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

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
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.save),
              onPressed: _saving ? null : _save,
            ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(_error!,
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: Colors.redAccent)),
                  ),
                )
              : widget.plugin.capabilityDetails.isEmpty
                  ? const Center(child: Text('No capabilities to configure'))
                  : ListView(
                      padding: const EdgeInsets.all(16),
                      children:
                          widget.plugin.capabilityDetails.map(_buildCard).toList(),
                    ),
    );
  }

  Widget _buildCard(PluginCapability cap) {
    final cfg = _caps[cap.id]!;
    final enabled = cfg['enabled'] as bool;
    final params = cfg['params'] as Map<String, dynamic>;

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ExpansionTile(
        initiallyExpanded: enabled,
        title: Text(cap.id,
            style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: cap.description.isEmpty ? null : Text(cap.description),
        trailing: Switch(
          value: enabled,
          onChanged: (v) => setState(() => cfg['enabled'] = v),
        ),
        children: [
          if (cap.properties.isEmpty)
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('No parameters',
                  style: TextStyle(color: Colors.grey)),
            )
          else
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: cap.properties.entries.map((e) {
                  return _buildParamField(
                    key: e.key,
                    schema: Map<String, dynamic>.from(e.value as Map),
                    value: params[e.key],
                    enabled: enabled,
                    onChanged: (v) => setState(() => params[e.key] = v),
                  );
                }).toList(),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildParamField({
    required String key,
    required Map<String, dynamic> schema,
    required dynamic value,
    required bool enabled,
    required ValueChanged<dynamic> onChanged,
  }) {
    final type = schema['type'] as String? ?? 'string';
    final desc = schema['description'] as String? ?? '';
    final enums = schema['enum'] as List<dynamic>?;
    final def = schema['default'];

    Widget field;
    if (enums != null && enums.isNotEmpty) {
      field = DropdownButtonFormField<dynamic>(
        value: value ?? enums.first,
        items: enums
            .map((v) => DropdownMenuItem(value: v, child: Text(v.toString())))
            .toList(),
        onChanged: enabled ? onChanged : null,
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    } else if (type == 'boolean') {
      field = Switch(
        value: value == true,
        onChanged: enabled ? onChanged : null,
      );
    } else if (type == 'integer') {
      field = TextFormField(
        initialValue: value?.toString() ?? '',
        enabled: enabled,
        keyboardType: TextInputType.number,
        inputFormatters: [FilteringTextInputFormatter.digitsOnly],
        onChanged: (v) => onChanged(int.tryParse(v)),
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    } else if (type == 'number') {
      field = TextFormField(
        initialValue: value?.toString() ?? '',
        enabled: enabled,
        keyboardType: const TextInputType.numberWithOptions(decimal: true),
        onChanged: (v) => onChanged(double.tryParse(v)),
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          isDense: true,
        ),
      );
    } else {
      field = TextFormField(
        initialValue: value?.toString() ?? '',
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
              Text(key, style: const TextStyle(fontWeight: FontWeight.w500)),
              if (def != null) ...[
                const SizedBox(width: 8),
                Text('(default: $def)',
                    style: TextStyle(
                        fontSize: 12,
                        color: Colors.grey.shade600,
                        fontStyle: FontStyle.italic)),
              ],
            ],
          ),
          if (desc.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2, bottom: 4),
              child: Text(desc,
                  style:
                      TextStyle(fontSize: 12, color: Colors.grey.shade600)),
            ),
          field,
        ],
      ),
    );
  }
}