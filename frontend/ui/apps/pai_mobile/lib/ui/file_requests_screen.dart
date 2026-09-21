import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/websocket_provider.dart';
import '../services/file_service.dart';

class FileRequestsScreen extends ConsumerStatefulWidget {
  const FileRequestsScreen({super.key});

  @override
  ConsumerState<FileRequestsScreen> createState() => _FileRequestsScreenState();
}

class _FileRequestsScreenState extends ConsumerState<FileRequestsScreen> {
  String? _userId;

  @override
  void initState() {
    super.initState();
    final envUser = const String.fromEnvironment('USER_ID', defaultValue: '');
    _userId = envUser.isEmpty ? null : envUser;
    _loadExisting();
  }

  Future<void> _loadExisting() async {
    try {
      final res = await FileService.listRequests(userId: _userId);
      final list = (res['requests'] as List<dynamic>? ?? []).cast<Map<String, dynamic>>();
      for (final r in list) {
        ref.read(fileRequestProvider.notifier).addRequest(r);
      }
    } catch (_) {}
  }

  Future<void> _approve(int id) async {
    try {
      await FileService.approveRequest(id);
      ref.read(fileRequestProvider.notifier).updateRequest({'request_id': id, 'status': 'approved'});
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Approve failed: $e')));
    }
  }

  Future<void> _deny(int id) async {
    try {
      await FileService.denyRequest(id);
      ref.read(fileRequestProvider.notifier).updateRequest({'request_id': id, 'status': 'denied'});
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Deny failed: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final requests = ref.watch(fileRequestProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('File Access Requests')),
      body: requests.isEmpty
          ? const Center(child: Text('No pending requests'))
          : ListView.separated(
              itemCount: requests.length,
              separatorBuilder: (context, index) => const Divider(),
              itemBuilder: (context, index) {
                final r = requests[index];
                final id = (r['id'] ?? r['request_id']) as int? ?? 0;
                final status = r['status'] as String? ?? 'pending';
                return ListTile(
                  title: Text(r['path'] ?? ''),
                  subtitle: Text('Requester: ${r['requester'] ?? 'agent'} • Status: $status'),
                  trailing: status == 'pending'
                      ? Row(mainAxisSize: MainAxisSize.min, children: [
                          IconButton(icon: const Icon(Icons.check), onPressed: () => _approve(id)),
                          IconButton(icon: const Icon(Icons.close), onPressed: () => _deny(id)),
                        ])
                      : null,
                );
              },
            ),
    );
  }
}
