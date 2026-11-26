import 'package:flutter/material.dart';

import '../../../plugins/runtime/plugin_runtime.dart';
import '../../theme/theme.dart';

/// Shown with the Android Files plugin settings: shared folders (Pictures, Download, DCIM...) only work once
/// the user switches on "All files access" for PAI in Android Settings (only the user can; PAI cannot ask in
/// a dialog). Says whether it is on and opens the right Android screen. Hidden where there is no such
/// thing (every platform but Android). Re-checks when the user comes back from Settings.
class AllFilesAccessNotice extends StatefulWidget {
  /// Test seams: answer instead of asking the native side.
  final Future<bool?> Function()? statusProbe;
  final Future<bool> Function()? openSettings;

  const AllFilesAccessNotice({super.key, this.statusProbe, this.openSettings});

  @override
  State<AllFilesAccessNotice> createState() => _AllFilesAccessNoticeState();
}

class _AllFilesAccessNoticeState extends State<AllFilesAccessNotice> with WidgetsBindingObserver {
  final _runtime = PluginRuntime();
  bool? _granted;
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
    if (state == AppLifecycleState.resumed) _refresh();
  }

  Future<void> _refresh() async {
    final g = await (widget.statusProbe ?? _runtime.allFilesAccessGranted)();
    if (!mounted) return;
    setState(() {
      _granted = g;
      _loaded = true;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (!_loaded || _granted == null) return const SizedBox.shrink();
    final on = _granted!;
    final scheme = Theme.of(context).colorScheme;
    return Container(
      key: const Key('all-files-access-notice'),
      padding: const EdgeInsets.all(PaiSpacing.md),
      decoration: BoxDecoration(
        color: (on ? scheme.primaryContainer : scheme.tertiaryContainer).withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(on ? Icons.check_circle_outline : Icons.folder_special_outlined, size: 20),
          const SizedBox(width: PaiSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(on ? 'All files access is on' : 'Turn on All files access',
                    style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: 2),
                Text(
                  on
                      ? 'PAI can reach the shared folders you allowed above. You can turn it off in Android Settings > Special app access > All files access > PAI.'
                      : 'Folders like Pictures and Download are only reachable once you switch on "All files access" for PAI: Settings > Special app access > All files access > PAI. Until then PAI can only use its own folder.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                if (!on)
                  Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton(
                      key: const Key('open-all-files-access'),
                      onPressed: () async => (widget.openSettings ?? _runtime.openAllFilesAccessSettings)(),
                      child: const Text('Open Android settings'),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
