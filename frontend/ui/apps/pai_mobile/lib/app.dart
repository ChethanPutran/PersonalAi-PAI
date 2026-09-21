import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import 'auth/auth_service.dart';
import 'config/app_config.dart';
import 'core/api/api_client.dart';

import 'device/device_info.dart';
import 'device/device_registration.dart';
import 'device/device_connection_service.dart';

import 'chat/chat_service.dart';

import 'plugins/plugin_manager.dart';
import 'plugins/plugin_service.dart';
import 'plugins/plugin_installer.dart';
import 'plugins/plugin_repository.dart';
import 'plugins/plugin_registry.dart';
import 'plugins/plugin_command_handler.dart';
import 'plugins/plugin_command_router.dart';
import 'plugins/runtime/plugin_runtime.dart';

import 'tasks/task_service.dart';

import 'ui/auth/login_screen.dart';
import 'ui/chat/chat_screen.dart';
import 'ui/plugins/plugins_screen.dart';
import 'ui/tasks/tasks_screen.dart';


class PaiApp extends StatefulWidget {
  const PaiApp({super.key});

  @override
  State<PaiApp> createState() => _PaiAppState();
}

class _PaiAppState extends State<PaiApp> {
  late final AuthService auth;
  late final ApiClient api;

  String? deviceId;
  DeviceInfo? _device;
  PluginManager? pluginManager;
  PluginService? pluginService;
  PluginCommandHandler? pluginCommandHandler;
  DeviceConnectionService? connectionService;

  bool _booting = true;
  bool _initializing = false;
  String? _initError;

  int page = 0;

  @override
  void initState() {
    super.initState();
    auth = AuthService(baseUrl: AppConfig.baseUrl);
    api = ApiClient(baseUrl: AppConfig.baseUrl, auth: auth);
    _bootstrap();
  }

  // ----------------------------------------------------------------
  // Boot
  // ----------------------------------------------------------------

  Future<void> _bootstrap() async {
    await auth.restore();

    if (!mounted) return;
    setState(() => _booting = false);

    if (auth.isAuthenticated) {
      unawaited(_initialize());
    }
  }

  // ----------------------------------------------------------------
  // Called from LoginScreen on success, or from _bootstrap if a token
  // is already on disk.
  // ----------------------------------------------------------------

  Future<void> _initialize() async {
    if (_initializing) return;
    setState(() {
      _initializing = true;
      _initError = null;
    });

    try {
      // --------------------------------------------------------
      // 1. Register device (backend assigns device id, attached
      //    to the authenticated user).
      // --------------------------------------------------------
      final registration = DeviceRegistration(api);
      final device = await registration.register();
      _device = device;
      final id = device.id;
      debugPrint('[PaiApp] Device registered: $id');

      // --------------------------------------------------------
      // 2. Plugin runtime + service.
      // --------------------------------------------------------
      final runtime = PluginRuntime();
      final repository = PluginRepository(
        baseUrl: AppConfig.pluginRegistryUrl,
      );
      final installer = PluginInstaller(repository);
      final registry = PluginRegistry();

      final service = PluginService(
        installer: installer,
        repository: repository,
        runtime: runtime,
        registry: registry,
      );

      await service.restoreInstalled();

      // --------------------------------------------------------
      // 3. UI-facing manager.
      // --------------------------------------------------------
      final manager = PluginManager(
        service: service,
        api: api,
        deviceId: id,
      );

      await manager.loadCatalog(
        platform: device.platform,
        architecture: device.architecture,
      );

      // --------------------------------------------------------
      // 4. Command router + handler.
      // --------------------------------------------------------
      final commandRouter = PluginCommandRouter(service: service);
      service.attachRouter(commandRouter);
      final commandHandler = PluginCommandHandler(router: commandRouter);

      // --------------------------------------------------------
      // 5. WebSocket — with the JWT.
      // --------------------------------------------------------
      final connection = DeviceConnectionService(
        baseUrl: api.baseUrl,
        deviceId: id,
        token: auth.token,
      );

      connection.setMessageHandler((message) async {
        await _handleDeviceMessage(message, commandHandler);
      });

      if (!mounted) {
        connection.dispose();
        return;
      }

      setState(() {
        deviceId = id;
        pluginManager = manager;
        pluginService = service;
        pluginCommandHandler = commandHandler;
        connectionService = connection;
        _initializing = false;
      });

      await connection.connect();

      debugPrint('[PaiApp] Initialization complete');
    } on UnauthorizedException {
      debugPrint('[PaiApp] Token rejected — logging out');
      await _logout();
    } catch (e, st) {
      debugPrint('[PaiApp] Initialization failed: $e');
      debugPrintStack(stackTrace: st);

      if (!mounted) return;
      setState(() {
        _initializing = false;
        _initError = e.toString();
      });
    }
  }

  // ----------------------------------------------------------------
  // Reconcile (called on WS connect)
  // ----------------------------------------------------------------

  Future<void> _reconcilePlugins() async {
    final manager = pluginManager;
    final device = _device;
    if (manager == null || device == null) return;

    await manager.reconcileWithBackend();

    await manager.loadCatalog(
      platform: device.platform,
      architecture: device.architecture,
    );
    debugPrint('[PaiApp] Plugin state reconciled + catalog refreshed');
  }

  // ----------------------------------------------------------------
  // Device messages
  // ----------------------------------------------------------------

  Future<void> _handleDeviceMessage(
    Map<String, dynamic> message,
    PluginCommandHandler commandHandler,
  ) async {
    final type = message['type']?.toString();

    switch (type) {
      case 'plugin.install':
      case 'plugin.enable':
      case 'plugin.disable':
      case 'plugin.uninstall':
      case 'plugin.execute':
      case 'capability.invoke':
        await _handlePluginCommand(message, commandHandler);
        return;

      case 'connected':
        debugPrint('[PaiApp] Backend connection confirmed');
        unawaited(_reconcilePlugins());
        return;

      case 'heartbeat_ack':
        return;

      case 'command':
      case 'event':
      case 'result':
        debugPrint('[PaiApp] $type received: $message');
        return;

      default:
        debugPrint('[PaiApp] Unknown message type: $type');
    }
  }

  Future<void> _handlePluginCommand(
    Map<String, dynamic> message,
    PluginCommandHandler commandHandler,
  ) async {
    final requestId = message['request_id']?.toString();
    final type = message['type']?.toString();

    try {
      final response = await commandHandler.handle(message);
      final success = response['success'] == true;

      final connection = connectionService;
      if (connection == null) {
        debugPrint('[PaiApp] Cannot send result: connection is null');
        return;
      }

      await connection.sendResult(
        requestId: requestId ?? 'unknown',
        success: success,
        result: response['result'],
        error: response['error']?.toString(),
      );

      if (success) {
        pluginManager?.handleCommandResult({
          'success': true,
          'result': response['result'],
          'error': null,
        });
      } else {
        pluginManager?.handleCommandFailure({
          'success': false,
          'result': response['result'],
          'error': response['error'],
        });
      }
    } catch (e, st) {
      debugPrint('[PaiApp] Plugin command failed: $e');
      debugPrintStack(stackTrace: st);

      try {
        await connectionService?.sendResult(
          requestId: requestId ?? 'unknown',
          success: false,
          result: {'operation': type},
          error: e.toString(),
        );
      } catch (sendError) {
        debugPrint('[PaiApp] Failed to send error: $sendError');
      }
    }
  }

  // ----------------------------------------------------------------
  // Logout
  // ----------------------------------------------------------------

  Future<void> _logout() async {
    connectionService?.dispose();
    connectionService = null;

    deviceId = null;
    _device = null;
    pluginManager = null;
    pluginService = null;
    pluginCommandHandler = null;

    await auth.logout();

    if (!mounted) return;
    setState(() {
      _initializing = false;
      _initError = null;
    });
  }

  // ----------------------------------------------------------------
  // Dispose
  // ----------------------------------------------------------------

  @override
  void dispose() {
    connectionService?.dispose();
    super.dispose();
  }

  // ----------------------------------------------------------------
  // Build
  // ----------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    // 1. Booting — reading token from disk.
    if (_booting) {
      return const MaterialApp(
        debugShowCheckedModeBanner: false,
        home: Scaffold(
          body: Center(child: CircularProgressIndicator()),
        ),
      );
    }

    // 2. Not authenticated — show login.
    if (!auth.isAuthenticated) {
      return MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
          useMaterial3: true,
        ),
        home: LoginScreen(
          auth: auth,
          onAuthenticated: () {
            if (!mounted) return;
            setState(() {});
            unawaited(_initialize());
          },
        ),
      );
    }

    // 3. Init error — allow retry or logout.
    if (_initError != null) {
      return MaterialApp(
        debugShowCheckedModeBanner: false,
        home: Scaffold(
          body: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.error_outline, size: 48, color: Colors.red),
                  const SizedBox(height: 16),
                  const Text(
                    'Failed to connect to PAI',
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _initError!,
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: Colors.grey),
                  ),
                  const SizedBox(height: 24),
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      OutlinedButton(
                        onPressed: _logout,
                        child: const Text('Sign out'),
                      ),
                      const SizedBox(width: 12),
                      ElevatedButton(
                        onPressed: _initialize,
                        child: const Text('Retry'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    }

    // 4. Still initializing.
    if (deviceId == null ||
        pluginManager == null ||
        connectionService == null) {
      return const MaterialApp(
        debugShowCheckedModeBanner: false,
        home: Scaffold(
          body: Center(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                CircularProgressIndicator(),
                SizedBox(height: 16),
                Text('Connecting to PAI...'),
              ],
            ),
          ),
        ),
      );
    }

    // 5. Full app.
    final screens = [
      ChatScreen(service: ChatService(api), deviceId: deviceId!),
      TasksScreen(service: TaskService(api)),
      PluginsScreen(manager: pluginManager!),
    ];

    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
        useMaterial3: true,
      ),
      home: Scaffold(
        appBar: AppBar(
          title: const Text('PAI'),
          actions: [
            IconButton(
              icon: const Icon(Icons.logout),
              tooltip: 'Sign out',
              onPressed: _logout,
            ),
          ],
        ),
        body: screens[page],
        bottomNavigationBar: NavigationBar(
          selectedIndex: page,
          onDestinationSelected: (i) => setState(() => page = i),
          destinations: const [
            NavigationDestination(icon: Icon(Icons.chat), label: 'Chat'),
            NavigationDestination(icon: Icon(Icons.task), label: 'Tasks'),
            NavigationDestination(
                icon: Icon(Icons.extension), label: 'Plugins'),
          ],
        ),
      ),
    );
  }
}