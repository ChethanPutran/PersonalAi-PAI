import 'package:flutter/material.dart';

import '../../../plugins/plugin_settings.dart';
import '../../theme/theme.dart';
import 'plugin_settings_form.dart';

/// Shown BEFORE installing a plugin that declares settings: the user picks them, then the install starts
/// with them. [onInstall] gets only what the user changed from the defaults (an empty map = defaults) and does
/// the install; it must show its own errors, except [PluginSettingsException] (the server refused a value):
/// that keeps the dialog open with the message under the field. Returns true once the install call returned,
/// null when the user cancelled.
Future<bool?> showPluginInstallDialog(
  BuildContext context, {
  required String pluginName,
  required String targetName,
  required bool isThisDevice,
  required PluginSettings settings,
  required Future<void> Function(Map<String, dynamic> config) onInstall,
  Widget? footer,
}) {
  return showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (_) => _InstallDialog(
      pluginName: pluginName,
      targetName: targetName,
      isThisDevice: isThisDevice,
      settings: settings,
      onInstall: onInstall,
      footer: footer,
    ),
  );
}

class _InstallDialog extends StatefulWidget {
  final String pluginName;
  final String targetName;
  final bool isThisDevice;
  final PluginSettings settings;
  final Future<void> Function(Map<String, dynamic> config) onInstall;
  final Widget? footer;
  const _InstallDialog({
    required this.pluginName,
    required this.targetName,
    required this.isThisDevice,
    required this.settings,
    required this.onInstall,
    this.footer,
  });

  @override
  State<_InstallDialog> createState() => _InstallDialogState();
}

class _InstallDialogState extends State<_InstallDialog> {
  late Map<String, dynamic> _current = Map<String, dynamic>.from(widget.settings.effective);
  Map<String, String> _errors = {};
  String? _message;
  bool _busy = false;

  Future<void> _install() async {
    setState(() {
      _busy = true;
      _errors = {};
      _message = null;
    });
    try {
      await widget.onInstall(settingsChanges(widget.settings, widget.settings.effective, _current));
      if (mounted) Navigator.of(context).pop(true);
    } on PluginSettingsException catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _errors = e.fieldErrors;
        _message = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return AlertDialog(
      key: const Key('plugin-install-dialog'),
      title: Text('Install ${widget.pluginName}'),
      content: SizedBox(
        width: 480,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                widget.isThisDevice
                    ? 'Choose how this plugin may work on this device. You can change this later in the plugin settings.'
                    : 'Choose how this plugin may work on ${widget.targetName}. You can change this later in the plugin settings.',
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(height: PaiSpacing.md),
              PluginSettingsForm(
                settings: widget.settings,
                initial: widget.settings.effective,
                errors: _errors,
                enabled: !_busy,
                onChanged: (v) => _current = v,
              ),
              if (widget.footer != null) widget.footer!,
              if (_message != null)
                Padding(
                  padding: const EdgeInsets.only(top: PaiSpacing.sm),
                  child: Text(_message!, key: const Key('install-dialog-message'), style: TextStyle(color: scheme.error)),
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          key: const Key('install-dialog-cancel'),
          onPressed: _busy ? null : () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          key: const Key('install-dialog-install'),
          onPressed: _busy ? null : _install,
          child: Text(_busy ? 'Installing…' : 'Install'),
        ),
      ],
    );
  }
}
