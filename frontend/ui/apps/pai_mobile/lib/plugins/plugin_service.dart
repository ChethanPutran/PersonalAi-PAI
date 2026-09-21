class PluginService {
  final PluginManager manager;
  final PluginInstaller installer;
  final PluginRepository repository;
  final PluginCommandRouter router;

  PluginService({
    required this.manager,
    required this.installer,
    required this.repository,
    required this.router,
  });

  Future<List<Map<String, dynamic>>> browse() async {
    final index = await repository.fetchIndex();
    return (index['plugins'] as List).cast<Map<String, dynamic>>();
  }

  Future<void> install(String id, String version) async {
    final manifest = await installer.install(id, version);
    await manager.install(manifest);
  }

  Future<bool> enable(String id) => manager.enable(id);
  Future<void> disable(String id) => manager.disable(id);

  Future<void> uninstall(String id) async {
    await manager.uninstall(id);
    await installer.remove(id);
  }

  Future<Map<String, dynamic>> invoke(String cap, Map<String, dynamic> p) =>
      router.route(cap, p);
}