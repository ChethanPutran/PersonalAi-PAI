import 'package:flutter/material.dart';
import 'dart:convert';

import '../../core/api/api_client.dart';
import '../../config/app_config.dart';

class PluginDetailScreen extends StatefulWidget {
  final String pluginId;
  const PluginDetailScreen({required this.pluginId, super.key});

  @override
  State<PluginDetailScreen> createState() => _PluginDetailScreenState();
}

class _PluginDetailScreenState extends State<PluginDetailScreen> {
  final _api = ApiClient(baseUrl: AppConfig.baseUrl);

  Map<String, dynamic>? _details;
  bool _loading = true;
  String? _error;
  Map<String, dynamic> _configValues = {};
  Map<String, dynamic>? _configSchema;
  bool _saving = false;

  final _newKeyController = TextEditingController();
  final _newValueController = TextEditingController();

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
    setState(() { _loading = true; _error = null; });
    try {
      final d = await _api.get('/api/v1/plugins/${widget.pluginId}');
      final map = Map<String, dynamic>.from(d as Map);
      _details = map;
      _configSchema = Map<String, dynamic>.from(map['config_schema'] ?? {});
      final saved = Map<String, dynamic>.from(map['config'] ?? {});
      _configValues = {};
      if (_configSchema!.isNotEmpty) {
        _configSchema!.forEach((k, s) {
          _configValues[k] = saved.containsKey(k) ? saved[k] : s['default'];
        });
      } else {
        _configValues = saved;
      }
    } catch (e) {
      _error = 'Failed to fetch details: $e';
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final r = await _api.post(
        '/api/v1/plugins/${widget.pluginId}/config',
        _configValues,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Saved: ${(r as Map)['status'] ?? 'ok'}')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Save failed: $e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  // ─── _buildSchemaForm, _buildGenericForm, _buildGenericRow unchanged ───
  // (copy them verbatim from your existing file; they don't reference
  //  PluginService)

  @override
  Widget build(BuildContext context) {
    // unchanged
    // (uses _loading, _error, _details, _configSchema, _configValues,
    //  _buildSchemaForm, _buildGenericForm, _save, _load)
    return Scaffold(
      appBar: AppBar(
        title: Text('Plugin: ${widget.pluginId}'),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _load),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : const SizedBox.shrink(), // replace with the previous body
    );
  }
}