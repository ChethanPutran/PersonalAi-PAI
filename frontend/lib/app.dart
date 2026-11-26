import 'dart:async';

import 'plugins/permissions/permission_labels.dart';
import 'plugins/reconcile_gate.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:pai_mobile/device/repositories/device_repository.dart';
import 'package:pai_mobile/notifications/notification_service.dart';
import 'package:pai_mobile/notifications/push_service.dart';
import 'package:pai_mobile/protocol/device_enums.dart';
import 'package:pai_mobile/schedules/schedule_service.dart';
import 'package:pai_mobile/ui/schedules/schedules_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'auth/auth_service.dart';
import 'config/app_config.dart';
import 'core/api/api_client.dart';

import 'models/device.dart';
import 'device/device_registration.dart';
import 'device/device_connection_service.dart';

import 'chat/chat_service.dart';

import 'plugins/permission_reporter.dart';
import 'plugins/plugin_manager.dart';
import 'plugins/plugin_service.dart';
import 'plugins/plugin_installer.dart';
import 'plugins/plugin_repository.dart';
import 'plugins/plugin_registry.dart';
import 'plugins/plugin_command_handler.dart';
import 'plugins/plugin_command_router.dart';
import 'plugins/remote_lifecycle.dart';
import 'device/remote_access.dart';
import 'plugins/runtime/plugin_runtime.dart';

import 'plugins/capability_dispatcher.dart';

import 'tasks/task_event_stream.dart';
import 'tasks/task_service.dart';

import 'ui/auth/login_screen.dart';
import 'ui/chat/chat_screen.dart';
import 'ui/plugins/plugins_screen.dart';
import 'ui/tasks/tasks_screen.dart';
import 'profile/profile_service.dart';
import 'profile/settings_service.dart';
import 'ui/profile/profile_screen.dart';
import 'ui/shell/pai_shell.dart';
import 'ui/theme/theme.dart';
import 'ui/widgets/hud/hud.dart';

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
  String? deviceId;
    final GlobalKey<NavigatorState> _navigatorKey =
    GlobalKey<NavigatorState>();


  DeviceInfo? _device;

  PluginManager? _pluginManager;
  CapabilityDispatcher? _dispatcher;
  LocalTaskRunner? _testTaskRunner;

  ChatService? _chatService;
  TaskService? _taskService;
  ScheduleService? _scheduleService;
  DeviceConnectionService? _connectionService;

  /// What the owner allows other devices to do to this one (pushed by the backend; see
  /// [RemoteAccessController]) and the device-side handling of install requests.
  RemoteAccessController? _remoteAccess;
  RemoteLifecycle? _remoteLifecycle;

  ProfileService? _profileService;
  DeviceRepository? _deviceRepository;

  late final SettingsService _settingsService = SettingsService(
    initial: {'notifications': true, 'auto_sync': true},
    onChanged: (values) async {
      final prefs = await SharedPreferences.getInstance();
      for (final e in values.entries) {
        await prefs.setBool('settings.${e.key}', e.value);
      }
    },
  );

  /// The per-user live task feed (SSE `GET /tasks/events`): owned here, started after sign-in,
  /// stopped on logout. The Tasks screens consume it; nothing polls.
  TaskEventStream? _taskEventStream;

  /// Broadcast stream of `task.*` / `plan.*` events received over the device WebSocket.
  /// (Still used by the Reminders screen; the Tasks screens use [_taskEventStream].)
  final _taskEventsController =
      StreamController<Map<String, dynamic>>.broadcast(); // NEW
  String? _initialUserId;
  bool _booting = true;
  bool _initializing = false;
  String? _initError;
  static const int _remindersTabIndex = 3;

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

    // A schedule fired while the app is open: have the task list reload once. The server finishes
    // the task a moment after sending the push, so wait briefly. (The live feed normally delivers
    // this by itself; this covers a feed that missed it.)
    PushService.onScheduleFired = (_) {
      Future<void>.delayed(const Duration(seconds: 2), () {
        if (mounted) _taskEventStream?.requestRefresh('schedule_fired');
      });
    };

    _bootstrap();
  }

  Future<bool> _requestPluginPermissionConsent({
  required String pluginName,
  required String nativeModule,
  required List<String> permissions,
}) async {
  final context = _navigatorKey.currentContext;

if (context == null || !mounted) {
    debugPrint(
      '[PaiApp] consent skipped for $pluginName: '
      'context=${context != null} mounted=$mounted',
    );
    return false;
  }
   debugPrint(
    '[PaiApp] consent dialog for $pluginName '
    '($nativeModule): $permissions',
  );

  // A remote install is waiting for this answer: tell whoever asked.
  unawaited(_remoteLifecycle?.awaitingPermission() ?? Future<void>.value());

  final approved = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (dialogContext) => AlertDialog(
      title: Text('$pluginName requires permission'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('This plugin is requesting access to:'),
          const SizedBox(height: 12),
          ...permissions.map((permission) {
            final label = describePermission(permission);
            final warn = Theme.of(dialogContext).colorScheme.error;
            return Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(
                    label.sensitive ? Icons.warning_amber_rounded : Icons.security,
                    size: 18,
                    color: label.sensitive ? warn : null,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(label.title, style: const TextStyle(fontWeight: FontWeight.w600)),
                        if (label.detail.isNotEmpty)
                          Text(label.detail, style: Theme.of(dialogContext).textTheme.bodySmall),
                      ],
                    ),
                  ),
                ],
              ),
            );
          }),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(dialogContext, false),
          child: const Text('Deny'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(dialogContext, true),
          child: const Text('Allow'),
        ),
      ],
    ),
  );

  debugPrint('[PaiApp] consent result for $pluginName: $approved');
  unawaited(_remoteLifecycle?.permissionAnswered() ?? Future<void>.value());
  return approved == true;
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
      final id = device.id;
      debugPrint('[PaiApp] Device registered: $id');

      // From now on every call names this device (the backend tells local from remote with it).
      api.deviceId = id;

      // The owner's switches for THIS device. Nothing remote is allowed until the backend says so.
      _remoteAccess?.dispose();
      final remoteAccess = RemoteAccessController(api: api, deviceId: id);

      // WebSocket.
      final connection = DeviceConnectionService(
        baseUrl: api.baseUrl,
        deviceId: id,
        token: auth.token,
        remoteAccess: remoteAccess,
      );

      // Restore chat session (if any) so that the user can see their
      // previous messages and continue the conversation.
      final chatService = ChatService(api);
      await chatService.restoreSession();

      // --------------------------------------------------------
      // 2. Plugin runtime + service.
      // --------------------------------------------------------
      final runtime = PluginRuntime();
      final repository = PluginRepository(api: api);
      final installer = PluginInstaller(repository);
      final registry = PluginRegistry();

      final pluginService = PluginService(
        installer: installer,
        repository: repository,
        runtime: runtime,
        registry: registry,
      );

      await pluginService.restoreInstalled();

      final scheduleService = ScheduleService(api: api);
      final taskService = TaskService(api);

      // The live task feed. A 401 here means the session is dead → same flow as any other
      // unauthorized API call. The previous stream (re-login) is stopped first.
      _taskEventStream?.dispose();
      final taskEvents = TaskEventStream(
        url: api.urlFor('/tasks/events'),
        headers: () => api.authHeaders,
        onUnauthorized: auth.handleUnauthorized,
      )..start();
      final profileService = ProfileService(api: api);
      final deviceRepo = DeviceRepository(profileService: profileService);

      // Device side of install requests: enforce the switch, report stages + the final result.
      final remoteLifecycle = RemoteLifecycle(
        thisDeviceId: () => deviceId,
        access: () => remoteAccess.value,
        send: (frame) => connection.send(frame),
        reportInstall: ({required pluginId, required version, operationId, required success, error}) =>
            _pluginManager?.reportCommandInstall(
                  pluginId: pluginId,
                  version: version,
                  operationId: operationId,
                  success: success,
                  error: error,
                ) ??
            Future<void>.value(),
        reportUninstall: ({required pluginId, operationId, required success, error}) =>
            _pluginManager?.reportCommandUninstall(
                  pluginId: pluginId,
                  operationId: operationId,
                  success: success,
                  error: error,
                ) ??
            Future<void>.value(),
        bindStages: (listener) => installer.onStage = listener,
      );

      // 4. Command router + handler.
      final commandRouter = PluginCommandRouter(
  service: pluginService,
  onPermissionConsent: _requestPluginPermissionConsent,
);
      // pluginService.attachRouter(commandRouter);
      final commandHandler = PluginCommandHandler(router: commandRouter);

      // 5. Capability dispatcher — drives PluginRuntime when the
      //    backend sends an `invoke` frame from DeviceExecutor.
      final dispatcher = CapabilityDispatcher(
        registry: registry,
        installer: installer,
        runtime: runtime,
        abi: device.architecture,
      );

      // --------------------------------------------------------
      // 3. UI-facing manager.
      // --------------------------------------------------------
      final pluginManager = PluginManager(
        service: pluginService,
        api: api,
        deviceId: id,
        dispatcher: dispatcher,
        onPermissionConsent: _requestPluginPermissionConsent,
        permissionReporter: PermissionReporter(
          api: api,
          deviceId: id,
          status: runtime.permissionStatus,
        ),
      );

      // Load the catalog so that the PluginManager has the latest plugin
      // information before the user sees the PluginsScreen. This ensures
      // that the user sees the most up-to-date plugin information when they
      await pluginManager.loadCatalog();

      final testRunner = LocalTaskRunner(dispatcher: dispatcher);

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
      connection.setInvokeHandler(({
        required capability,
        required parameters,
      }) async {
        final result = await _runBuiltinOrDispatch(capability, parameters);
        // Coerce to Map — matches what the protocol needs.
        if (result is Map<String, dynamic>) return result;
        if (result is Map) return result.cast<String, dynamic>();
        return {'result': result};
      });

      if (!mounted) {
        connection.dispose();
        unawaited(taskEvents.dispose());
        return;
      }

      setState(() {
        _taskEventStream = taskEvents;
        deviceId = id;
        _device = device;
        _pluginManager = pluginManager;
        _connectionService = connection;
        _remoteAccess = remoteAccess;
        _remoteLifecycle = remoteLifecycle;
        _dispatcher = dispatcher;
        _chatService = chatService;
        _taskService = taskService;
        _scheduleService = scheduleService;
        _profileService = profileService;
        _deviceRepository = deviceRepo;
        _testTaskRunner = testRunner;
        _initializing = false;
      });

      await connection.connect();

// Ensure the initial device card reflects ONLINE the moment WS is up.
unawaited(_refreshDevicesAfterConnect());

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

  /// At most one reconcile in flight and one per minute: a socket that reconnects repeatedly
  /// must not repeat reconcile + catalog + update requests each time.
  final ReconcileGate _reconcileGate = ReconcileGate();

  Future<void> _reconcilePlugins() async {
    final manager = _pluginManager;
    final dispatcher = _dispatcher;

    if (manager == null || dispatcher == null) return;

    final ran = await _reconcileGate.run(() async {
      final ok = await manager.reconcileWithBackend();
      // The catalog was loaded at startup: only reload it when the reconcile changed something.
      await manager.loadCatalog(maxAge: manager.lastReconcileChangedBackend ? null : const Duration(minutes: 1));
      dispatcher.invalidateCache();
      return ok;
    });

    debugPrint(ran
        ? '[PaiApp] Plugin state reconciled + catalog refreshed'
        : '[PaiApp] Reconcile skipped (one ran recently or is in progress)');
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

    debugPrint('[PaiApp] Device message received: $type : $message');

    switch (type) {
      // ---- NEW: DeviceExecutor invoke ----
      case 'invoke':
        await _handleInvoke(message);
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

      case 'sync_plugins_request':
        await _handlePluginSyncRequest(message);
        return;

      case 'sync_plugins_response':
        await _handlePluginSyncResponse(message);
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
      case 'plugin.operation':
        // Live status of a remote install/uninstall (replaces polling).
        _pluginManager?.handleOperationFrame(message);
        return;

      case 'device.remote_access':
        // Applied by the connection service itself (it enforces it); nothing else to do.
        return;

      case 'device.removed':
        final removedDeviceId = message['device_id']?.toString();

        if (removedDeviceId != null) {
          _handleDeviceRemoved(removedDeviceId);
        }
        return;
     case 'connected':
        debugPrint('[PaiApp] Backend connection confirmed');

        // Backend has just run attach_connection → DB status is now ONLINE.
        // Pull the authoritative view and republish to every consumer.
        unawaited(_refreshDevicesAfterConnect());

        // Pick up installs that were running while the socket was down.
        unawaited(_pluginManager?.loadActiveOperations() ?? Future<void>.value());

        unawaited(
          _reconcilePlugins().catchError((e, st) {
            debugPrint('[PaiApp] reconcile failed: $e\n$st');
          }),
        );
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
    final connection = _connectionService;
    if (connection == null) {
      debugPrint('[PaiApp] Cannot invoke: device connection unavailable');
      return;
    }

    final requestId = message['request_id']?.toString() ?? '';
    final capability = message['capability']?.toString() ?? '';

    final parameters =
        (message['parameters'] as Map?)?.cast<String, dynamic>() ?? {};

    try {
      final result = await _runBuiltinOrDispatch(capability, parameters);

      await connection.sendResult(
        requestId: requestId,
        success: true,
        result: result,
      );
    } catch (e, st) {
      debugPrint('[PaiApp] invoke failed: $e\n$st');

      await connection.sendResult(
        requestId: requestId,
        success: false,
        error: e.toString(),
      );
    }
  }

Future<void> _refreshDevicesAfterConnect() async {
  final repo = _deviceRepository;
  final id = deviceId;
  final userId = auth.userId;

  if (repo == null || id == null || userId == null) {
    debugPrint(
      '[PaiApp] cannot refresh devices yet '
      '(repo=${repo != null} id=$id user=$userId)',
    );
    return;
  }

  // Optimistically flip the local copy so the UI updates instantly.
  final current = _device;
  if (current != null && current.id == id && mounted) {
    setState(() => _device = current.copyWith(
          status: 'online',
          connected: true,
          isCurrent: true,
        ));
  }

  // Then fetch authoritative state from the backend.
  try {
    await repo.loadDevices(userId: userId, currentDeviceId: id);
    if (!mounted) return;
    final refreshed = repo.getDevice(id);
    if (refreshed != null) {
      setState(() => _device = refreshed);
    }
    debugPrint(
      '[PaiApp] device list refreshed after WS connect: '
      '${repo.devices.length} device(s), current=${refreshed?.status}',
    );
  } catch (e) {
    debugPrint('[PaiApp] device refresh after connect failed: $e');
  }
}

  Future<dynamic> _runBuiltinOrDispatch(
    String capability,
    Map<String, dynamic> parameters,
  ) async {
    switch (capability) {
      case 'system.notifications.show':
        await NotificationService.show(
          title: parameters['title']?.toString() ?? 'PAI reminder',
          body: parameters['body']?.toString() ?? '',
          id: DateTime.now().millisecondsSinceEpoch & 0x7fffffff,
        );

        return {'shown': true};
    }

    final dispatcher = _dispatcher;

    if (dispatcher == null) {
      throw StateError('Plugin dispatcher is not initialized.');
    }

    return dispatcher.invoke(capability: capability, parameters: parameters);
  }

  // ----------------------------------------------------------------
  // Plugin sync (NEW)
  // ----------------------------------------------------------------
  Future<void> _handlePluginSyncRequest(Map<String, dynamic> message) async {
    final connection = _connectionService;
    final manager = _pluginManager;

    if (connection == null || manager == null) {
      debugPrint('[PaiApp] Cannot sync plugins: services not initialized');
      return;
    }

    try {
      final plugins = manager.plugins
          .where((plugin) => manager.isInstalled(plugin.id))
          .map((plugin) {
            return {
              'id': plugin.id,
              'version': plugin.version,
              'enabled': manager.isEnabled(plugin.id),
              'capabilities': plugin.capabilities,
            };
          })
          .toList(growable: false);

      await connection.send({
        'type': 'sync_plugins_response',
        'device_id': deviceId,
        'plugins': plugins,
        'timestamp': DateTime.now().toUtc().toIso8601String(),
      });

      debugPrint(
        '[PaiApp] → sync_plugins_response '
        '(${plugins.length} plugins)',
      );
    } catch (e, st) {
      debugPrint('[PaiApp] Plugin sync failed: $e');
      debugPrintStack(stackTrace: st);
    }
  }

  Future<void> _handlePluginSyncResponse(Map<String, dynamic> message) async {
    final manager = _pluginManager;
    if (manager == null) return;

    final targetDeviceId = message['device_id']?.toString();

    if (targetDeviceId == null || targetDeviceId.isEmpty) {
      debugPrint('[PaiApp] Sync response missing device_id');
      return;
    }

    // Ignore our own snapshot; local state comes from PluginService.
    if (targetDeviceId == deviceId) return;

    final plugins = message['plugins'] as List? ?? const [];

    manager.updateDevicePluginStates(targetDeviceId, plugins);

    debugPrint(
      '[PaiApp] Updated plugin states for device $targetDeviceId '
      '(${plugins.length} plugins)',
    );
  }

  // ----------------------------------------------------------------
  // Existing plugin-command bridge (unchanged)
  // ----------------------------------------------------------------

  Future<void> _handlePluginCommand(
    Map<String, dynamic> message,
    PluginCommandHandler commandHandler,
  ) async {
    final requestId = message['request_id']?.toString() ?? 'unknown';
    final type = message['type']?.toString() ?? 'unknown';

    final connection = _connectionService;
    final manager = _pluginManager;
    final dispatcher = _dispatcher;

    if (connection == null || manager == null) {
      debugPrint(
        '[PaiApp] Cannot handle plugin command: services not initialized',
      );
      return;
    }

    // Install/uninstall asked by the backend: the owner's switch decides (fail closed), and the
    // requester gets live stages plus a clear result.
    final lifecycle = _remoteLifecycle;
    final isLifecycle = lifecycle != null && RemoteLifecycle.handles(type);
    if (isLifecycle) {
      final refusal = await lifecycle.refuseIfNotAllowed(message);
      if (refusal != null) {
        try {
          await connection.sendResult(
            requestId: requestId,
            success: false,
            result: {'operation': type, 'plugin_id': RemoteLifecycle.pluginIdOf(message)},
            error: refusal,
          );
        } catch (e) {
          debugPrint('[PaiApp] Failed to send refusal: $e');
        }
        return;
      }
      lifecycle.begin(message);
    }

    try {
      final response = await commandHandler.handle(message);
      final success = response['success'] == true;

      // Send command result to backend.
      await connection.sendResult(
        requestId: requestId,
        success: success,
        result: response['result'],
        error: response['error']?.toString(),
      );

      if (success) {
        // Plugin installation, removal, enablement or disablement
        // may have changed capability resolution.
        dispatcher?.invalidateCache();

        manager.handleCommandResult({
          'success': true,
          'result': response['result'],
          'error': null,
        });

        debugPrint('[PaiApp] Plugin command succeeded: $type');
        if (isLifecycle) await lifecycle.succeeded(message);
      } else {
        manager.handleCommandFailure({
          'success': false,
          'result': response['result'],
          'error': response['error'],
        });

        debugPrint(
          '[PaiApp] Plugin command failed: $type - ${response['error']}',
        );
        if (isLifecycle) await lifecycle.failed(message, _commandFailureText(response['error'] ?? 'failed'));
      }
    } catch (e, st) {
      debugPrint('[PaiApp] Plugin command exception: $e');
      debugPrintStack(stackTrace: st);

      manager.handleCommandFailure({
        'success': false,
        'result': {'operation': type},
        'error': e.toString(),
      });
      if (isLifecycle) await lifecycle.failed(message, _commandFailureText(e));

      try {
        await connection.sendResult(
          requestId: requestId,
          success: false,
          result: {'operation': type},
          error: e.toString(),
        );
      } catch (sendError, sendStack) {
        debugPrint('[PaiApp] Failed to send command error: $sendError');
        debugPrintStack(stackTrace: sendStack);
      }
    } finally {
      if (isLifecycle) lifecycle.end();
    }
  }

  /// What the backend is told when an install request failed here. A permission the user hasn't
  /// granted yet gets its own wording (the backend turns it into "allow it on the device").
  String _commandFailureText(Object error) {
    if (error is PluginPermissionPendingException || error is PluginPermissionDeniedException) {
      return 'Permission needed: $error';
    }
    return error.toString();
  }

  void _handleDeviceRemoved(String removedDeviceId) {
    _pluginManager?.removeDevicePluginStates(removedDeviceId);
  }

  // ----------------------------------------------------------------
  // Logout
  // ----------------------------------------------------------------

  Future<void> _logout() async {
    _connectionService?.dispose();
    _testTaskRunner?.dispose();
    unawaited(_taskEventStream?.dispose() ?? Future<void>.value());
    _taskEventStream = null;

    _connectionService = null;
    _remoteLifecycle = null;
    _remoteAccess?.dispose();
    _remoteAccess = null;
    api.deviceId = null;
    _testTaskRunner = null;
    _pluginManager = null;
    _dispatcher = null;
    _chatService = null;
    _taskService = null;
    _scheduleService = null;
    _profileService = null;
    _deviceRepository = null;
    _device = null;

    deviceId = null;
    _initialUserId = null;

    await PushService.unregisterDevice();
    await auth.logout();

    if (!mounted) return;

    setState(() {
      page = 0;
      _initializing = false;
      _initError = null;
    });
  }

  // ----------------------------------------------------------------
  // Dispose
  // ----------------------------------------------------------------

  @override
  void dispose() {
    unawaited(_taskEventStream?.dispose() ?? Future<void>.value());
    _taskEventsController.close();
    _testTaskRunner?.dispose();
    _connectionService?.dispose();
    _remoteAccess?.dispose();
    super.dispose();
  }

  // ----------------------------------------------------------------
  // Build
  // ----------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    // 1. Booting.
    if (_booting) {
      return MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: PaiTheme.light(),
        darkTheme: PaiTheme.dark(),
        themeMode: ThemeMode.dark,
        home: const _BootScreen(message: 'Initializing'),
      );
    }

    // 2. Not authenticated.
    if (!auth.isAuthenticated) {
      return MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: PaiTheme.light(),
        darkTheme: PaiTheme.dark(),
        themeMode: ThemeMode.dark,
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
        theme: PaiTheme.light(),
        darkTheme: PaiTheme.dark(),
        themeMode: ThemeMode.dark,
        home: HudScaffold(
          body: HudErrorState(
            title: 'Failed to connect to PAI',
            message: 'The assistant could not be reached. Check that the server is running '
                'and that this device can reach it.',
            details: _initError!,
            onRetry: _initialize,
            retryLabel: 'Retry',
          ),
          bottomNavigationBar: SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(PaiSpacing.lg),
              child: Align(
                alignment: Alignment.center,
                heightFactor: 1,
                child: GlowButton(
                  label: 'Sign out',
                  icon: Icons.logout_rounded,
                  variant: GlowVariant.ghost,
                  onPressed: _logout,
                ),
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
        _pluginManager == null ||
        _dispatcher == null ||
        _chatService == null ||
        _taskService == null ||
        _scheduleService == null ||
        _profileService == null ||
        _deviceRepository == null ||
        _device == null ||
        _testTaskRunner == null) {
      return MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: PaiTheme.light(),
        darkTheme: PaiTheme.dark(),
        themeMode: ThemeMode.dark,
        home: const _BootScreen(message: 'Connecting to PAI'),
      );
    }

    // 5. Full app.
    final screens = <Widget>[
      ChatScreen(service: _chatService!, deviceId: deviceId!),
      TasksScreen(
        service: _taskService!,
        live: _taskEventStream,
      ),
      PluginsScreen(
        manager: _pluginManager!,
        deviceRepository: _deviceRepository!,
        thisDevice: _device,
      ),
      SchedulesScreen(
        service: _scheduleService!,
        taskService: _taskService!,
        events: _taskEventsController.stream,
        profileService: _profileService,
        currentDeviceId: deviceId,
      ),
      ProfileScreen(
        service: _profileService!,
        settings: _settingsService,
        currentDeviceId: deviceId,
        remoteAccess: _remoteAccess,
        onLogout: _logout,
      ),
      if (kDebugMode)
        TestTaskScreen(
          pluginManager: _pluginManager!,
          runner: _testTaskRunner!,
        ),
    ];
    // Capture once; keep stable across frames.
    final appKey = _initialUserId ??= auth.userId;
    return MaterialApp(
      // When the user changes, Flutter sees a different key → destroys the
      // old widget tree (calling dispose on ChatScreen, etc.) and builds
      // fresh. Without this, IndexedStack keeps the old user's ChatScreen
      // (and its messages, sessions, status WS) alive across logins.
      key: ValueKey('app-${appKey ?? "anon"}'),
      navigatorKey: _navigatorKey,
      debugShowCheckedModeBanner: false,
      theme: PaiTheme.light(),
        darkTheme: PaiTheme.dark(),
        themeMode: ThemeMode.dark,
      home: PaiShell(
        index: page,
        onSelect: (i) => setState(() => page = i),
        onSignOut: _logout,
        body: IndexedStack(index: page, children: screens),
        destinations: [
          const PaiDestination(icon: Icons.forum_outlined, selectedIcon: Icons.forum, label: 'Chat'),
          const PaiDestination(icon: Icons.checklist_rtl_outlined, selectedIcon: Icons.checklist_rtl, label: 'Tasks'),
          const PaiDestination(icon: Icons.extension_outlined, selectedIcon: Icons.extension, label: 'Plugins'),
          const PaiDestination(icon: Icons.alarm_outlined, selectedIcon: Icons.alarm, label: 'Reminders'),
          const PaiDestination(icon: Icons.person_outline, selectedIcon: Icons.person, label: 'Profile'),
          if (kDebugMode)
            const PaiDestination(icon: Icons.bug_report_outlined, selectedIcon: Icons.bug_report, label: 'Test'),
        ],
      ),
    );
  }
}


/// Full-screen "initializing / connecting" state: the orb thinking, with a label.
class _BootScreen extends StatelessWidget {
  const _BootScreen({required this.message});
  final String message;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return HudScaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const ArcReactorOrb(state: OrbState.thinking, size: 180),
            const SizedBox(height: PaiSpacing.xl),
            Text(message.toUpperCase(),
                style: Theme.of(context).textTheme.titleMedium?.copyWith(color: scheme.primary, letterSpacing: 4)),
          ],
        ),
      ),
    );
  }
}
