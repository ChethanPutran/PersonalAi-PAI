import 'package:flutter/material.dart';

import '../../plugins/plugin_settings.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';
import 'widgets/plugin_settings_form.dart';

/// A plugin's settings on ONE device (this one or another the user owns), built from its `configSchema`.
///
/// [load] and [save] talk to the server (`GET` / `PUT /plugins/device/{device}/{plugin}/config`); they are
/// injected so the screen runs without a network in tests. Changing another device's settings needs that
/// device's "Allow remote plugin install" switch: the server's refusal is shown in plain words.
class PluginSettingsScreen extends StatefulWidget {
  final String pluginName;
  final String deviceName;
  final bool isThisDevice;
  final Future<PluginSettings> Function() load;

  /// Receives only the changed keys (a key back at its default is `null`, the server's "reset").
  final Future<PluginSettings> Function(Map<String, dynamic> changes) save;

  /// Extra content under the form (for example the Android "All files access" notice).
  final Widget? footer;

  const PluginSettingsScreen({
    super.key,
    required this.pluginName,
    required this.deviceName,
    required this.isThisDevice,
    required this.load,
    required this.save,
    this.footer,
  });

  @override
  State<PluginSettingsScreen> createState() => _PluginSettingsScreenState();
}

class _PluginSettingsScreenState extends State<PluginSettingsScreen> {
  PluginSettings? _settings;
  Map<String, dynamic> _base = {};
  Map<String, dynamic> _current = {};
  int _formGeneration = 0;
  bool _loading = true;
  bool _saving = false;
  String? _loadError;
  String? _message;
  bool _messageIsError = false;
  bool _remoteOff = false;
  Map<String, String> _fieldErrors = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = null;
    });
    try {
      final s = await widget.load();
      if (!mounted) return;
      setState(() {
        _settings = s;
        _base = Map<String, dynamic>.from(s.effective);
        _current = Map<String, dynamic>.from(s.effective);
        _formGeneration++;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loadError = e is PluginSettingsException ? e.message : "Couldn't load the settings.";
        _loading = false;
      });
    }
  }

  Map<String, dynamic> get _changes => _settings == null ? const {} : settingsChanges(_settings!, _base, _current);

  Future<void> _save() async {
    final changes = _changes;
    if (changes.isEmpty) return;
    setState(() {
      _saving = true;
      _message = null;
      _fieldErrors = {};
      _remoteOff = false;
    });
    try {
      final s = await widget.save(changes);
      if (!mounted) return;
      setState(() {
        _settings = s;
        _base = Map<String, dynamic>.from(s.effective);
        _current = Map<String, dynamic>.from(s.effective);
        _formGeneration++;
        _saving = false;
        _messageIsError = false;
        _message = widget.isThisDevice ? 'Saved.' : 'Saved. ${widget.deviceName} applies it as soon as it is online.';
      });
    } on PluginSettingsException catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _messageIsError = true;
        _message = e.message;
        _fieldErrors = e.fieldErrors;
        _remoteOff = e.remoteOff;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _messageIsError = true;
        _message = "Couldn't save the settings.";
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return HudScaffold(
      appBar: HudAppBar(
        title: '${widget.pluginName} settings',
        actions: [
          IconButton(icon: const Icon(Icons.refresh), tooltip: 'Reload', onPressed: _loading || _saving ? null : _load),
        ],
      ),
      body: _body(context),
    );
  }

  Widget _body(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_loadError != null) {
      return HudErrorState(title: "Couldn't load settings", details: _loadError, onRetry: _load);
    }
    final s = _settings!;
    if (!s.hasSettings) {
      return const HudEmptyState(
        icon: Icons.tune,
        title: 'No settings',
        message: 'This plugin has nothing to configure.',
      );
    }
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final changed = _changes.isNotEmpty;
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 860),
        child: ListView(
          padding: const EdgeInsets.all(PaiSpacing.lg),
          children: [
            Text(
              widget.isThisDevice ? 'On this device' : 'On ${widget.deviceName}',
              key: const Key('settings-target'),
              style: context.pai.mono(size: 12),
            ),
            const SizedBox(height: PaiSpacing.md),
            PluginSettingsForm(
              key: ValueKey('form-$_formGeneration'),
              settings: s,
              initial: _current,
              errors: _fieldErrors,
              enabled: !_saving,
              onChanged: (v) => setState(() {
                _current = v;
                _message = null;
                _fieldErrors = {};
              }),
            ),
            if (widget.footer != null) ...[KeyedSubtree(key: ValueKey('footer-$_formGeneration'), child: widget.footer!), const SizedBox(height: PaiSpacing.lg)],
            if (_message != null)
              Padding(
                padding: const EdgeInsets.only(bottom: PaiSpacing.md),
                child: Row(
                  key: const Key('settings-message'),
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(_messageIsError ? Icons.error_outline : Icons.check_circle_outline,
                        size: 18, color: _messageIsError ? scheme.error : scheme.primary),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(_message!, style: text.bodyMedium?.copyWith(color: _messageIsError ? scheme.error : scheme.primary)),
                          if (_remoteOff)
                            Text(
                              'Open Profile on ${widget.deviceName} and turn on "Allow remote plugin install", then try again.',
                              key: const Key('settings-remote-hint'),
                              style: text.bodySmall,
                            ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            GlowButton(
              key: const Key('settings-save'),
              label: _saving ? 'Saving…' : 'Save settings',
              icon: Icons.check,
              expanded: true,
              loading: _saving,
              onPressed: changed && !_saving ? _save : null,
            ),
          ],
        ),
      ),
    );
  }
}
