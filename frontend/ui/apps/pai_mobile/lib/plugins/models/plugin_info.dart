import 'plugin_capability.dart';

class PluginInfo {
  final String id;              // "camera"
  final String name;            // "Camera"
  final String version;         // "1.0.0"
  final String description;
  final List<PluginCapability> capabilities;
  final List<String> permissions;
  final Map<String, dynamic> metadata;

  const PluginInfo({
    required this.id,
    required this.name,
    required this.version,
    required this.description,
    required this.capabilities,
    required this.permissions,
    this.metadata = const {},
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'version': version,
        'description': description,
        'capabilities': capabilities.map((c) => c.toJson()).toList(),
        'permissions': permissions,
        'metadata': metadata,
      };

  static const camera = PluginInfo(
    id: 'camera',
    name: 'Camera',
    version: '1.0.0',
    description: 'Capture photos and record video using the device camera.',
    permissions: ['camera', 'microphone', 'filesystem'],
    capabilities: [
      PluginCapability(
        id: 'camera.capture',
        description: 'Take a still photo.',
        permissions: ['camera', 'filesystem'],
        parametersSchema: {
          'type': 'object',
          'properties': {
            'lens': {'type': 'string', 'enum': ['front', 'back'], 'default': 'back'},
            'resolution': {'type': 'string', 'default': 'high'},
            'outputPath': {'type': 'string'},
            'format': {'type': 'string', 'enum': ['jpeg', 'png'], 'default': 'jpeg'},
          },
        },
      ),
      PluginCapability(
        id: 'camera.record',
        description: 'Record a video.',
        permissions: ['camera', 'microphone', 'filesystem'],
        parametersSchema: {
          'type': 'object',
          'properties': {
            'lens': {'type': 'string', 'enum': ['front', 'back'], 'default': 'back'},
            'durationMs': {'type': 'integer'},
            'outputPath': {'type': 'string'},
            'audio': {'type': 'boolean', 'default': true},
          },
        },
      ),
      PluginCapability(
        id: 'camera.stream',
        description: 'Open a live preview stream (frames via channel).',
        permissions: ['camera'],
      ),
    ],
  );
}