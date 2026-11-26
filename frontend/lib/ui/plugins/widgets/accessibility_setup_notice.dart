import 'package:flutter/material.dart';
import '../../../plugins/runtime/plugin_runtime.dart';
import '../../theme/theme.dart';

/// Shown under the Android "Computer control" plugin: it cannot work until the user turns on PAI's
/// Accessibility Service in Android Settings (only the user can: the system shows its own warning).
/// Re-checks when the user comes back from Settings.
class AccessibilitySetupNotice extends StatefulWidget {
  /// Test seam: answer instead of asking the native side.
  final Future<({bool enabled, bool connected})?> Function()? statusProbe;
  final Future<bool> Function()? openSettings;

  const AccessibilitySetupNotice({super.key, this.statusProbe, this.openSettings});

  @override
  State<AccessibilitySetupNotice> createState() => _AccessibilitySetupNoticeState();
}

class _AccessibilitySetupNoticeState extends State<AccessibilitySetupNotice> with WidgetsBindingObserver {
  final _runtime = PluginRuntime();
  ({bool enabled, bool connected})? _status;
  bool _loaded = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refresh();       // back from Android Settings
  }

  Future<void> _refresh() async {
    final s = await (widget.statusProbe ?? _runtime.accessibilityStatus)();
    if (!mounted) return;
    setState(() {
      _status = s;
      _loaded = true;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (!_loaded || _status == null) return const SizedBox.shrink();     // not Android, or unknown: say nothing
    final on = _status!.enabled;
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(PaiSpacing.lg, 0, PaiSpacing.lg, PaiSpacing.sm),
      child: Container(
        key: const Key('accessibility-setup-notice'),
        padding: const EdgeInsets.all(PaiSpacing.md),
        decoration: BoxDecoration(
          color: (on ? scheme.primaryContainer : scheme.tertiaryContainer).withValues(alpha: 0.5),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(on ? Icons.check_circle_outline : Icons.accessibility_new, size: 20),
            const SizedBox(width: PaiSpacing.sm),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(on ? 'Computer control is on' : 'Turn on computer control',
                      style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 2),
                  Text(
                    on
                        ? 'PAI can read the screen and tap, type and swipe when you ask it to. You can turn it off in Android Settings > Accessibility at any time.'
                        : 'To let PAI look at the screen and tap, type and swipe for you, turn on "PAI computer control" in Android Settings > Accessibility. Android will show its own warning; PAI only acts when you ask it to.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                  if (!on)
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton(
                        key: const Key('open-accessibility-settings'),
                        onPressed: () async => (widget.openSettings ?? _runtime.openAccessibilitySettings)(),
                        child: const Text('Open Android settings'),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
