import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/websocket_provider.dart';
import '../services/preferences_service.dart';

class DeviceSelectionScreen extends ConsumerStatefulWidget {
  const DeviceSelectionScreen({super.key});

  @override
  ConsumerState<DeviceSelectionScreen> createState() => _DeviceSelectionScreenState();
}

class _DeviceSelectionScreenState extends ConsumerState<DeviceSelectionScreen> {
  String _input = 'mobile';
  String _output = 'mobile';

  final List<String> _devices = ['mobile', 'laptop', 'watch', 'earbuds', 'none'];

  @override
  Widget build(BuildContext context) {
    final ws = ref.read(websocketProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Device Selection')),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Input Device', style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            DropdownButton<String>(
              value: _input,
              items: _devices.map((d) => DropdownMenuItem(value: d, child: Text(d))).toList(),
              onChanged: (v) => setState(() => _input = v ?? _input),
            ),
            const SizedBox(height: 16),
            const Text('Output Device', style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            DropdownButton<String>(
              value: _output,
              items: _devices.map((d) => DropdownMenuItem(value: d, child: Text(d))).toList(),
              onChanged: (v) => setState(() => _output = v ?? _output),
            ),
            const Spacer(),
            Row(
              children: [
                Expanded(
                  child: ElevatedButton(
                    onPressed: () async {
                      // send context update for real-time change
                      ws.sendContextUpdate({'input_device': _input, 'output_device': _output});
                      // capture messenger before async gap
                      final messenger = ScaffoldMessenger.of(context);
                      // try to persist device selection to server
                      try {
                        await PreferencesService.registerDevice(
                          'default', // TODO: replace with actual user id when auth available
                          'device-${DateTime.now().millisecondsSinceEpoch}',
                          deviceName: 'mobile',
                          deviceType: 'mobile',
                          platform: 'flutter',
                          capabilities: {'input': _input, 'output': _output},
                        );
                      } catch (e) {
                        // ignore failures for now
                      }

                      if (!mounted) return;
                      messenger.showSnackBar(const SnackBar(content: Text('Device selection updated')));
                    },
                    child: const Text('Save'),
                  ),
                ),
                const SizedBox(width: 12),
                OutlinedButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('Close'),
                ),
              ],
            )
          ],
        ),
      ),
    );
  }
}
