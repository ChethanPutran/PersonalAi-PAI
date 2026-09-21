import 'package:flutter/material.dart';
import '../services/file_service.dart';

class FileBrowserScreen extends StatefulWidget {
  const FileBrowserScreen({super.key});

  @override
  State<FileBrowserScreen> createState() => _FileBrowserScreenState();
}

class _FileBrowserScreenState extends State<FileBrowserScreen> {
  String _currentPath = '.';
  List<dynamic> _entries = [];
  bool _loading = false;
  String? _error;
  String? _userId;

  @override
  void initState() {
    super.initState();
    final envUser = const String.fromEnvironment('USER_ID', defaultValue: '');
    _userId = envUser.isEmpty ? null : envUser;
    _load(_currentPath);
  }

  Future<void> _load(String path) async {
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final res = await FileService.listFiles(path);
      if (!mounted) return;
      setState(() {
        _currentPath = res['path'] as String? ?? path;
        _entries = res['entries'] as List<dynamic>? ?? [];
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _loading = false;
        });
      }
    }
  }

  Future<void> _openFile(String path) async {
    try {
      final data = await FileService.readFile(path);
      if (!mounted) return;
      if (data['type'] == 'text') {
        await showDialog(
          context: context,
          builder: (_) => AlertDialog(
            title: Text(data['path']),
            content: SingleChildScrollView(child: SelectableText(data['content'] ?? '')),
            actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('Close'))],
          ),
        );
      } else {
        await showDialog(
          context: context,
          builder: (_) => AlertDialog(
            title: Text(data['path']),
            content: Text('Binary file (${data['content_base64']?.length ?? 0} bytes base64)'),
            actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('Close'))],
          ),
        );
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed to open file: $e')));
    }
  }

  Future<void> _requestAccessForCurrentPath() async {
    final uid = _userId ?? '';
    if (uid.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('No user id configured')));
      return;
    }
    try {
      final res = await FileService.requestAccess(uid, _currentPath);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Requested access: ${res['status']}')));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Request failed: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('File Browser'),
        actions: [IconButton(icon: const Icon(Icons.refresh), onPressed: () => _load(_currentPath))],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text('Error: $_error'))
              : Stack(
                  children: [
                    ListView.builder(
                      itemCount: _entries.length,
                      itemBuilder: (context, index) {
                        final e = _entries[index] as Map<String, dynamic>;
                        return ListTile(
                          leading: Icon(e['is_dir'] == true ? Icons.folder : Icons.insert_drive_file),
                          title: Text(e['name'] ?? ''),
                          subtitle: Text(e['path'] ?? ''),
                          onTap: () {
                            if (e['is_dir'] == true) {
                              _load(e['path'] as String);
                            } else {
                              _openFile(e['path'] as String);
                            }
                          },
                        );
                      },
                    ),
                    Positioned(
                      bottom: 16,
                      right: 16,
                      child: FloatingActionButton.extended(
                        onPressed: _requestAccessForCurrentPath,
                        icon: const Icon(Icons.request_page),
                        label: const Text('Request Access'),
                      ),
                    )
                  ],
                ),
    );
  }
}
