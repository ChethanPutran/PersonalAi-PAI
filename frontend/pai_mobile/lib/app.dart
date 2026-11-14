import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:pai_mobile/notifications/notification_service.dart';
import 'package:pai_mobile/notifications/push_service.dart';
import 'package:pai_mobile/protocol/device_enums.dart';
import 'package:pai_mobile/schedules/schedule_service.dart';
import 'package:pai_mobile/ui/schedules/schedules_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';

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

import 'plugins/capability_dispatcher.dart';

import 'tasks/task_service.dart';

import 'ui/auth/login_screen.dart';
import 'ui/chat/chat_screen.dart';
import 'ui/plugins/plugins_screen.dart';
import 'ui/tasks/tasks_screen.dart';
import 'models/user_profile.dart';
import 'profile/profile_service.dart';
import 'profile/settings_service.dart';
import 'ui/profile/profile_screen.dart';

import 'tasks/local_task_runner.dart'; // NEW
import 'ui/tasks/test_task_screen.dart'; // NEW

class PaiApp extends StatefulWidget {
  const PaiApp({super.key});

  @override
  State<PaiApp> createState() => _PaiAppState();
}

class _PaiAppState extends State<PaiApp> {
  late final AuthService auth;
  late final ApiClient api;
  ChatService? _chatService;
  TaskService? _taskService;
  ScheduleService? _scheduleService;

  String? deviceId;
  DeviceInfo? _device;
  PluginManager? pluginManager;
  PluginService? pluginService;
  PluginCommandHandler? pluginCommandHandler;
  DeviceConnectionService? connectionService;
  CapabilityDispatcher? _dispatcher;
  LocalTaskRunner? _testTaskRunner;

  ProfileService? _profileService;
  // in _PaiAppState, replace the default construction:
  late final SettingsService _settingsService = SettingsService(
    initial: {'notifications': true, 'auto_sync': true},
    onChanged: (values) async {
      final prefs = await SharedPreferences.getInstance();
      for (final e in values.entries) {
        await prefs.setBool('settings.${e.key}', e.value);
      }
    },
  );

  /// Broadcast stream of `task.*` / `plan.*` events received over the WS.
  /// Fed by [_handleDeviceMessage]; consumed by TasksScreen for auto-refresh.
  final _taskEventsController =
      StreamController<Map<String, dynamic>>.broadcast(); // NEW

  bool _booting = true;
  bool _initializing = false;
  String? _initError;
  static const int _remindersTabIndex = 5;

  int page = 0;

  @override
  void initState() {
    super.initState();
    auth = AuthService(baseUrl: AppConfig.baseUrl);
    api = ApiClient(baseUrl: AppConfig.baseUrl, auth: auth);

    PushService.onNotificationTap = (data) {
  // Reminders tab is index 4 (Chat=0, Tasks=1, Plugins=2, Test=3, Profile=4, Reminders=5)
  // ← adjust if your order differs.
  if (mounted) setState(() => page = _remindersTabIndex);
};

    _bootstrap();
  }

  // ----------------------------------------------------------------
  // Boot
  // ----------------------------------------------------------------

  Future<void> _bootstrap() async {
    await auth.restore();

    if (!mounted) return;
    setState(() => _booting = false);

    await NotificationService.init();

    if (auth.isAuthenticated) {
      unawaited(_initialize());
    }
  }

  // ----------------------------------------------------------------
  // Initialize (after login or from restored token)
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
      final repository = PluginRepository(baseUrl: AppConfig.pluginRegistryUrl);
      final installer = PluginInstaller(repository);
      final registry = PluginRegistry();

      final service = PluginService(
        installer: installer,
        repository: repository,
        runtime: runtime,
        registry: registry,
      );

      await service.restoreInstalled();

      final chat = _chatService ??= ChatService(api);
      await chat.restoreSession();

      _scheduleService ??= ScheduleService(api: api);

      // TaskService was never being constructed. `build()` expects it
      // non-null, but nothing in `_initialize()` ever assigned it — the
      // `_taskService!` line threw every time the app finished booting.
      // `??=` matches the ChatService pattern so a Retry after a failed
      // init reuses the existing instance rather than creating a duplicate.
      final task = _taskService ??= TaskService(api);

      _profileService ??= ProfileService(api: api);

      _profileService ??= ProfileService(api: api);

      // --------------------------------------------------------
      // 3. UI-facing manager.
      // --------------------------------------------------------
      final manager = PluginManager(service: service, api: api, deviceId: id);

      await manager.loadCatalog();

      // 4. Command router + handler.
      final commandRouter = PluginCommandRouter(service: service);
      service.attachRouter(commandRouter);
      final commandHandler = PluginCommandHandler(router: commandRouter);

      // 5. Capability dispatcher (NEW) — drives PluginRuntime when the
      //    backend sends an `invoke` frame from DeviceExecutor.
      final dispatcher = CapabilityDispatcher(
        pluginManager: manager,
        installer: installer,
        runtime: runtime,
      );

      final testRunner = LocalTaskRunner(dispatcher: dispatcher); // NEW

      // 6. WebSocket.
      final connection = DeviceConnectionService(
        baseUrl: api.baseUrl,
        deviceId: id,
        token: auth.token,
      );

      // Add the message handler to the connection service so that when a message is received,
      // it will be dispatched to [_handleDeviceMessage].
      connection.setMessageHandler((message) async {
        await _handleDeviceMessage(message, commandHandler);
      });

      connection.setControlHandler(({required op, required requestId}) async {
        return await _handleDeviceControl(op: op, requestId: requestId);
      });

      // Add the invoke handler to the connection service so that when an 'invoke' message is received,
      // it will be dispatched to the CapabilityDispatcher.
      connection.setInvokeHandler(
      ({required capability, required parameters}) async {
        final result = await _runBuiltinOrDispatch(capability, parameters);
        // Coerce to Map — matches what the protocol needs.
        if (result is Map<String, dynamic>) return result;
        if (result is Map) return result.cast<String, dynamic>();
        return {'result': result};
      },
    );

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
        _dispatcher = dispatcher;
        _testTaskRunner = testRunner;
        _initializing = false;
      });

      await connection.connect();

      // Register for push (best-effort).
      unawaited(PushService.init(api: api));
      unawaited(PushService.registerDevice(id));

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

    await manager.loadCatalog();
    debugPrint('[PaiApp] Plugin state reconciled + catalog refreshed');

    // Any installs / uninstalls may have changed which plugin owns a
    // capability — drop the dispatcher cache so the next invoke re-reads
    // manifests from disk.
    _dispatcher?.invalidateCache();
  }

  // ------------------------------------------------
  // Device control messages (NEW) — invoked from [_handleDeviceMessage].
  // ------------------------------------------------
  Future<bool> _handleDeviceControl({
    required DeviceControlOp op,
    required String requestId,
  }) async {
    switch (op) {
      case DeviceControlOp.cancel:
        // `DeviceConnectionService._handleControl` already tracks the
        // cancellation internally and will discard the eventual result.
        // We have no atomic-op work to unwind here, so just acknowledge.
        return true;

      case DeviceControlOp.pause:
      case DeviceControlOp.resume:
        // No capability currently supports real pause/resume. Return
        // false so the backend knows to fall back to its wrapper-level
        // checkpoint. When a plugin (e.g. camera.record) starts
        // supporting pause, this handler forwards to it.
        return false;
    }
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
      // ---- NEW: DeviceExecutor invoke ----
      case 'invoke':
        await _handleInvoke(message);
        return;

      // ---- NEW: backend asks for plugin reconciliation ----
      case 'sync_plugins_request':
        await _handlePluginSyncRequest(message);
        return;

      // ---- NEW: task / plan events → broadcast for TasksScreen ----
      case 'task.created':
      case 'task.updated':
      case 'task.started':
      case 'task.completed':
      case 'task.failed':
      case 'task.paused':
      case 'task.resumed':
      case 'task.cancelled':
      case 'plan.created':
      case 'plan.completed':
      case 'plan.failed':
        _taskEventsController.add(message);
        return;

      // ---- Existing plugin-command protocol ----
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

  // ----------------------------------------------------------------
  // DeviceExecutor invoke (NEW)
  // ----------------------------------------------------------------

  Future<void> _handleInvoke(Map<String, dynamic> message) async {
    final requestId = message['request_id']?.toString() ?? '';
    final capability = message['capability']?.toString() ?? '';
    final parameters =
        (message['parameters'] as Map?)?.cast<String, dynamic>() ?? {};

    final connection = connectionService;

    if (connection == null) {
      debugPrint('[PaiApp] invoke dropped: connection null');
      return;
    }

    debugPrint('[PaiApp] invoke: $capability (req=$requestId)');

    try {
      final result = await _runBuiltinOrDispatch(capability, parameters);

      await connection.sendResult(
        requestId: requestId,
        success: true,
        result: result,
      );
      debugPrint('[PaiApp] → result for $requestId');
    } catch (e, st) {
      debugPrint('[PaiApp] invoke failed: $e\n$st');
      await connection.sendResult(
        requestId: requestId,
        success: false,
        error: e.toString(),
      );
    }
  }

  Future<dynamic> _runBuiltinOrDispatch(
    String capability,
    Map<String, dynamic> parameters,
  ) async {

    // --- Built-in capabilities shadow the plugin dispatcher ---
    switch (capability) {
      case 'system.notifications.show':
        await NotificationService.show(
          title: parameters['title']?.toString() ?? 'PAI reminder',
          body: parameters['body']?.toString() ?? '',
          id: DateTime.now().millisecondsSinceEpoch & 0x7fffffff,
        );
        return {'shown': true};
    }

    // ---- Everything else → plugin system ----
    final dispatcher = _dispatcher;
    if (dispatcher == null) {
      throw StateError('No dispatcher available');
    }
    return dispatcher.invoke(capability: capability, parameters: parameters);
  }

  // ----------------------------------------------------------------
  // Plugin sync (NEW)
  // ----------------------------------------------------------------

  Future<void> _handlePluginSyncRequest(Map<String, dynamic> message) async {
    final manager = pluginManager;
    final connection = connectionService;
    if (manager == null || connection == null) return;

    try {
      final plugins = manager.plugins
          .where((p) => p.isInstalled)
          .map(
            (p) => {
              'id': p.id,
              'version': p.version,
              'enabled': p.isEnabled,
              'capabilities': p.capabilityDetails
                  .map((c) => c.id)
                  .toList(growable: false),
            },
          )
          .toList(growable: false);

      await connection.send({
        'type': 'sync_plugins_response',
        'device_id': deviceId,
        'plugins': plugins,
        'timestamp': DateTime.now().toUtc().toIso8601String(),
      });
      debugPrint(
        '[PaiApp] → sync_plugins_response (${plugins.length} plugins)',
      );
    } catch (e) {
      debugPrint('[PaiApp] sync_plugins_response failed: $e');
    }
  }

  // ----------------------------------------------------------------
  // Existing plugin-command bridge (unchanged)
  // ----------------------------------------------------------------

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
    _dispatcher = null;
    _testTaskRunner?.dispose();
    _testTaskRunner = null;

    // These were not being reset. Without this, a re-login as a
    // different user reuses the cached ChatService (with the previous
    // user's `_sessionId`) and TaskService.
    _chatService = null;
    _taskService = null;
    _scheduleService = null;
    await PushService.unregisterDevice();   

    await auth.logout();

    if (!mounted) return;
    setState(() {
      _initializing = false;
      _initError = null;
      _profileService = null;
    });
  }

  // ----------------------------------------------------------------
  // Dispose
  // ----------------------------------------------------------------

  @override
  void dispose() {
    _taskEventsController.close(); // NEW
    _testTaskRunner?.dispose(); // NEW
    connectionService?.dispose();
    super.dispose();
  }

  // ----------------------------------------------------------------
  // Build
  // ----------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    // 1. Booting.
    if (_booting) {
      return const MaterialApp(
        debugShowCheckedModeBanner: false,
        home: Scaffold(body: Center(child: CircularProgressIndicator())),
      );
    }

    // 2. Not authenticated.
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

    // 3. Init error.
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
    //
    // Guard every field that the "full app" section below dereferences
    // with `!`. Previously only `deviceId`, `pluginManager`, and
    // `connectionService` were checked — but `_chatService`,
    // `_taskService`, `_testTaskRunner`, and `_profileService` are also
    // dereferenced with `!`, so a missing assignment in `_initialize()`
    // produced a raw "null check operator" crash instead of the
    // spinner. Better to show "Connecting..." for another frame than to
    // explode.
    if (deviceId == null ||
        pluginManager == null ||
        connectionService == null ||
        _chatService == null ||
        _taskService == null ||
        _testTaskRunner == null ||
        _profileService == null) {
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
    // build() — after the "still initializing" guard
    // By this point _initialize() has completed and set both. The
    // `??=` fallback was masking a real failure mode (a ChatService
    // that never restored its session).
    final chatService = _chatService!;
    final taskService = _taskService!;

    // 5. Full app.
    final screens = <Widget>[
      ChatScreen(service: chatService, deviceId: deviceId!),
      TasksScreen(
        service: taskService,
        taskEvents: _taskEventsController.stream,
      ),
      PluginsScreen(manager: pluginManager!),
      TestTaskScreen(pluginManager: pluginManager!, runner: _testTaskRunner!),
      SchedulesScreen(
       service: _scheduleService!,
       taskService: taskService,
  events: _taskEventsController.stream,
  profileService: _profileService,
  currentDeviceId: deviceId,
      ),
      ProfileScreen(
        service: _profileService!,
        settings: _settingsService,
        currentDeviceId: deviceId,
        onLogout: _logout,
      ),
    ];

    return MaterialApp(
      // When the user changes, Flutter sees a different key → destroys the
      // old widget tree (calling dispose on ChatScreen, etc.) and builds
      // fresh. Without this, IndexedStack keeps the old user's ChatScreen
      // (and its messages, sessions, status WS) alive across logins.
      key: ValueKey('app-${auth.userId ?? "anon"}'),
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
        // >>> The change <<<
        body: IndexedStack(index: page, children: screens),
        bottomNavigationBar: NavigationBar(
          selectedIndex: page,
          onDestinationSelected: (i) => setState(() => page = i),
          destinations: const [
            NavigationDestination(icon: Icon(Icons.chat), label: 'Chat'),
            NavigationDestination(icon: Icon(Icons.task), label: 'Tasks'),
            NavigationDestination(icon: Icon(Icons.extension),label: 'Plugins'),
            NavigationDestination(icon: Icon(Icons.bug_report), label: 'Test'),
            NavigationDestination(icon: Icon(Icons.alarm), label: 'Reminders'),
            NavigationDestination(icon: Icon(Icons.person), label: 'Profile'),
          ],
        ),
      ),
    );
  }
}
