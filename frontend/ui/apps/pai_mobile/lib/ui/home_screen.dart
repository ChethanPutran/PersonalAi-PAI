import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../services/preferences_service.dart';

import '../models/plugin_info.dart';
import '../providers/websocket_provider.dart';
import '../services/camera_service.dart';
import '../services/plugin_service.dart';
import '../services/voice_service.dart';
import 'plugins/plugin_detail_screen.dart';
import 'device_selection_screen.dart';
import 'file_browser_screen.dart';
import 'file_requests_screen.dart';

class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  final TextEditingController _textController = TextEditingController();
  final VoiceService _voice = VoiceService();
  bool _isVoiceListening = false;
  bool _isSubmittingGoal = false;
  final ScrollController _scrollController = ScrollController();
  late Future<List<PluginInfo>> _pluginsFuture;
  Map<String, dynamic>? _healthStatus;
  String? _errorMessage;

  @override
void initState() {
  super.initState();
  _voice.initialize();
  _pluginsFuture = _loadPlugins();
  _loadHealthStatus();

  // Check WebSocket connection after a delay
  WidgetsBinding.instance.addPostFrameCallback((_) {
    final ws = ref.read(websocketProvider);
    ws.ensureConnected().catchError((e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('WebSocket connection failed: $e')),
      );
    });
  });
}

  @override
  void dispose() {
    _textController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  Future<List<PluginInfo>> _loadPlugins() async {
    return PluginService.getPlugins();
  }

  Future<void> _loadHealthStatus() async {
    try {
      final health = await PluginService.getHealth();
      if (!mounted) return;
      setState(() {
        _healthStatus = health;
        _errorMessage = null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _errorMessage = 'Backend health check failed: $error';
      });
    }
  }

  Future<void> _refreshData() async {
    setState(() {
      _pluginsFuture = _loadPlugins();
    });
    await Future.wait([_pluginsFuture, _loadHealthStatus()]);
  }

   Future<void> _sendGoal(String goal) async {
    if (goal.trim().isEmpty) return;
    try {
      final ws = ref.read(websocketProvider);
      await ws.sendGoal(
        goal,
        context: {
          'device': 'mobile',
          'timestamp': DateTime.now().toIso8601String(),
        },
      );
      _textController.clear();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed to send goal: $e')),
      );
    }
  }
  void _sendVoiceGoal() async {
    if (_isVoiceListening) {
      _voice.stopListening();
      setState(() => _isVoiceListening = false);
      return;
    }
    setState(() => _isVoiceListening = true);
    _voice.startListening((recognized) async {
      _textController.text = recognized;
      await _sendGoal(recognized);
      setState(() => _isVoiceListening = false);
      _voice.stopListening();
    });
  }


 Future<void> _captureAndSend() async {
    final image = await CameraService.captureImage();
    if (image != null) {
      final bytes = await image.readAsBytes();
      final base64Image = base64Encode(bytes);

      final ws = ref.read(websocketProvider);
      await ws.sendGoal(
        'Analyze this image',
        context: {'image_data': base64Image, 'task': 'describe_scene'},
      );
    }
  }

  Future<void> _togglePlugin(PluginInfo plugin) async {
    setState(() {
      _isSubmittingGoal = true;
    });

    try {
      await PluginService.setPluginEnabled(plugin.id, !plugin.isEnabled);
      await _refreshData();
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed to update ${plugin.name}: $error')),
      );
    } finally {
      if (mounted) {
        setState(() {
          _isSubmittingGoal = false;
        });
      }
    }
  }

  Widget _buildHeader() {
    final backendUrl = dotenv.env['API_BASE_URL'] ?? 'http://localhost:8000';
    final status = _healthStatus?['status']?.toString() ?? 'checking';

    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFF0F172A), Color(0xFF1E293B), Color(0xFF334155)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(28),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Personal AI Mobile',
            style: TextStyle(
              color: Colors.white,
              fontSize: 30,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Connected to $backendUrl',
            style: TextStyle(
              color: Colors.white.withValues(alpha: 0.82),
              fontSize: 14,
            ),
          ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _InfoChip(
                label: 'Backend: $status',
                color: _healthStatus == null ? Colors.amber : Colors.green,
              ),
              _InfoChip(label: 'WebSocket goals', color: Colors.blueAccent),
              _InfoChip(label: 'Response mode', color: Colors.purple),
              _InfoChip(label: 'Plugin control', color: Colors.deepOrange),
            ],
          ),
          if (_errorMessage != null) ...[
            const SizedBox(height: 12),
            Text(
              _errorMessage!,
              style: const TextStyle(color: Colors.redAccent),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildResponseModeToggle() {
    final mode = _healthStatus?['response_mode'] as String? ?? 'text';
    final isVoice = mode == 'voice';
    return Row(
      children: [
        const Text('Response Mode:'),
        const SizedBox(width: 8),
        ChoiceChip(
          label: const Text('Text'),
          selected: !isVoice,
          onSelected: (v) async {
            if (v) {
              final ws = ref.read(websocketProvider);
              ws.sendContextUpdate({
                'preferences': {'response_mode': 'text'},
              });
              final uid = const String.fromEnvironment(
                'USER_ID',
                defaultValue: '',
              );
              if (uid.isNotEmpty) {
                await PreferencesService.setPreference(
                  uid,
                  'response_mode',
                  'text',
                );
              }
              await _loadHealthStatus();
            }
          },
        ),
        const SizedBox(width: 8),
        ChoiceChip(
          label: const Text('Voice'),
          selected: isVoice,
          onSelected: (v) async {
            if (v) {
              final ws = ref.read(websocketProvider);
              ws.sendContextUpdate({
                'preferences': {'response_mode': 'voice'},
              });
              final uid = const String.fromEnvironment(
                'USER_ID',
                defaultValue: '',
              );
              if (uid.isNotEmpty) {
                await PreferencesService.setPreference(
                  uid,
                  'response_mode',
                  'voice',
                );
              }
              await _loadHealthStatus();
            }
          },
        ),
      ],
    );
  }

  Widget _buildGoalComposer() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.9),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: Colors.white.withValues(alpha: 0.2)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Send Goal to Backend',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _textController,
            minLines: 1,
            maxLines: 4,
            decoration: InputDecoration(
              hintText: 'Ask the agent to do something',
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(16),
              ),
            ),
            onSubmitted: (value) => _sendGoal(value),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: ElevatedButton.icon(
                  onPressed: _isSubmittingGoal
                      ? null
                      : () => _sendGoal(_textController.text),
                  icon: const Icon(Icons.send),
                  label: const Text('Send via WebSocket'),
                ),
              ),
              const SizedBox(width: 12),
              IconButton.filledTonal(
                onPressed: _captureAndSend,
                icon: const Icon(Icons.camera_alt_outlined),
              ),
              const SizedBox(width: 8),
              IconButton.filledTonal(
                onPressed: _sendVoiceGoal,
                icon: Icon(_isVoiceListening ? Icons.mic : Icons.mic_none),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildPlugins(List<PluginInfo> plugins) {
    final enabledCount = plugins.where((plugin) => plugin.isEnabled).length;

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.9),
        borderRadius: BorderRadius.circular(24),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(
                child: Text(
                  'Plugins',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                ),
              ),
              Text(
                '$enabledCount / ${plugins.length} enabled',
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ],
          ),
          const SizedBox(height: 12),
          if (plugins.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 24),
              child: Center(child: Text('No plugins returned by the backend.')),
            )
          else
            ListView.separated(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              itemCount: plugins.length,
              separatorBuilder: (context, index) => const SizedBox(height: 12),
              itemBuilder: (context, index) {
                final plugin = plugins[index];
                final isEnabled = plugin.isEnabled;

                return Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: isEnabled
                        ? const Color(0xFFEFF6FF)
                        : const Color(0xFFF8FAFC),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(
                      color: isEnabled
                          ? const Color(0xFF60A5FA)
                          : const Color(0xFFE2E8F0),
                    ),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  plugin.name,
                                  style: const TextStyle(
                                    fontSize: 17,
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                                const SizedBox(height: 4),
                                Text(
                                  plugin.description,
                                  style: TextStyle(
                                    color: Colors.grey.shade700,
                                    height: 1.35,
                                  ),
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(width: 12),
                          Switch.adaptive(
                            value: isEnabled,
                            onChanged: _isSubmittingGoal
                                ? null
                                : (_) => _togglePlugin(plugin),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          _InfoChip(label: plugin.type, color: Colors.indigo),
                          _InfoChip(
                            label: 'v${plugin.version}',
                            color: Colors.teal,
                          ),
                          _InfoChip(
                            label: isEnabled ? 'Enabled' : 'Disabled',
                            color: isEnabled ? Colors.green : Colors.red,
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Align(
                        alignment: Alignment.centerRight,
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            TextButton.icon(
                              onPressed: _isSubmittingGoal
                                  ? null
                                  : () => _togglePlugin(plugin),
                              icon: Icon(
                                isEnabled ? Icons.toggle_off : Icons.toggle_on,
                              ),
                              label: Text(isEnabled ? 'Disable' : 'Enable'),
                            ),
                            const SizedBox(width: 8),
                            TextButton.icon(
                              onPressed: () {
                                Navigator.of(context).push(
                                  MaterialPageRoute(
                                    builder: (_) =>
                                        PluginDetailScreen(pluginId: plugin.id),
                                  ),
                                );
                              },
                              icon: const Icon(Icons.settings),
                              label: const Text('Configure'),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF1F5F9),
      appBar: AppBar(
        title: const Text('PAI Mobile Dashboard'),
        actions: [
          IconButton(
            tooltip: 'Refresh backend data',
            icon: const Icon(Icons.refresh),
            onPressed: _refreshData,
          ),
          IconButton(
            tooltip: 'Device selection',
            icon: const Icon(Icons.devices_other),
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => const DeviceSelectionScreen(),
                ),
              );
            },
          ),
          IconButton(
            tooltip: 'Browse files on host',
            icon: const Icon(Icons.folder_open),
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const FileBrowserScreen()),
              );
            },
          ),
          IconButton(
            tooltip: 'File requests',
            icon: const Icon(Icons.request_page),
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const FileRequestsScreen()),
              );
            },
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refreshData,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
          children: [
            _buildHeader(),
            const SizedBox(height: 16),
            _buildGoalComposer(),
            const SizedBox(height: 12),
            _buildResponseModeToggle(),
            const SizedBox(height: 16),
            FutureBuilder<List<PluginInfo>>(
              future: _pluginsFuture,
              builder: (context, snapshot) {
                if (snapshot.connectionState == ConnectionState.waiting) {
                  return const Padding(
                    padding: EdgeInsets.symmetric(vertical: 24),
                    child: Center(child: CircularProgressIndicator()),
                  );
                }

                if (snapshot.hasError) {
                  return Container(
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.9),
                      borderRadius: BorderRadius.circular(24),
                    ),
                    child: Text(
                      'Failed to load plugins: ${snapshot.error}',
                      style: const TextStyle(color: Colors.redAccent),
                    ),
                  );
                }

                final plugins = snapshot.data ?? const <PluginInfo>[];
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _buildPlugins(plugins),
                    const SizedBox(height: 16),
                    _buildActivityLog(),
                  ],
                );
              },
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildActivityLog() {
    final messages = ref.watch(messageProvider);

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.9),
        borderRadius: BorderRadius.circular(24),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Activity',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 8),
          if (messages.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Text('No recent activity'),
            )
          else
            ListView.separated(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              itemCount: messages.length,
              separatorBuilder: (context, index) => const SizedBox(height: 8),
              itemBuilder: (context, index) {
                final msg = messages[index];
                final result = msg.result ?? {};
                final agent =
                    result['agent'] ??
                    result['assigned_agent'] ??
                    result['executor'] ??
                    'kernel';
                final tools =
                    (result['tools'] ?? result['plugins'] ?? [])
                        as List<dynamic>? ??
                    [];

                return ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(msg.type == 'result' ? 'Result' : msg.type),
                  subtitle: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (msg.goal != null) Text(msg.goal!),
                      if (tools.isNotEmpty) Text('Tools: ${tools.join(', ')}'),
                      Text('Agent: $agent'),
                    ],
                  ),
                  trailing: IconButton(
                    icon: const Icon(Icons.info_outline),
                    onPressed: () {
                      showDialog(
                        context: context,
                        builder: (_) => AlertDialog(
                          title: const Text('Message details'),
                          content: SingleChildScrollView(
                            child: Text(result.toString()),
                          ),
                          actions: [
                            TextButton(
                              onPressed: () => Navigator.pop(context),
                              child: const Text('Close'),
                            ),
                          ],
                        ),
                      );
                    },
                  ),
                );
              },
            ),
        ],
      ),
    );
  }
}

class _InfoChip extends StatelessWidget {
  final String label;
  final Color color;

  const _InfoChip({required this.label, required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontWeight: FontWeight.w700,
          fontSize: 12,
        ),
      ),
    );
  }
}
