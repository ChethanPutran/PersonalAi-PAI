import 'package:flutter/material.dart';

import 'core/api/api_client.dart';

import 'device/device_registration.dart';
import 'device/device_connection_service.dart';

import 'chat/chat_service.dart';

import 'plugins/plugin_manager.dart';
import 'plugins/plugin_service2.dart';
import 'plugins/plugin_command_handler.dart';
import 'plugins/plugin_command_router.dart';
import 'plugins/plugin_registry.dart';
import 'plugins/platform/platform_plugin_registrar.dart';

import 'tasks/task_service.dart';

import 'ui/chat/chat_screen.dart';
import 'ui/plugins/plugins_screen.dart';
import 'ui/tasks/tasks_screen.dart';
import 'ui/home_screen.dart';

class PaiApp extends StatefulWidget {
  const PaiApp({super.key});

  @override
  State<PaiApp> createState() => _PaiAppState();
}

class _PaiAppState extends State<PaiApp> {
  late final ApiClient api;

  String? deviceId;

  PluginManager? pluginManager;

  PluginRegistry? pluginRegistry;

  PluginCommandHandler? pluginCommandHandler;

  DeviceConnectionService? connectionService;

  int page = 0;

  @override
  void initState() {
    super.initState();

    api = ApiClient();

    initialize();
  }

  // ==============================================================
  // INITIALIZATION
  // ==============================================================

  Future<void> initialize() async {
    try {
      /*
       * ------------------------------------------------------------
       * 1. Register device
       * ------------------------------------------------------------
       *
       * Backend generates the device ID.
       */
      final registration =
          DeviceRegistration(api);

      final device =
          await registration.register();

      final id = device.id;

      print(
        '[PaiApp] Device registered: $id',
      );

      /*
       * ------------------------------------------------------------
       * 2. Create device-side plugin registry
       * ------------------------------------------------------------
       *
       * This registry contains the actual plugin implementations
       * that run on this device.
       */
      final registry =
          PluginRegistry();

      PlatformPluginRegistrar.register(registry);

      /*
       * ------------------------------------------------------------
       * 3. Create plugin command router
       * ------------------------------------------------------------
       *
       * Backend:
       *
       * plugin.install
       * plugin.enable
       * plugin.disable
       * plugin.execute
       *
       * will be routed here.
       */
      final commandRouter =
          PluginCommandRouter(
        registry: registry,
      );

      /*
       * ------------------------------------------------------------
       * 4. Create plugin command handler
       * ------------------------------------------------------------
       *
       * Handles errors and converts command execution into a
       * success/failure response.
       */
      final commandHandler =
          PluginCommandHandler(
        router: commandRouter,
      );

      /*
       * ------------------------------------------------------------
       * 5. Create device WebSocket connection
       * ------------------------------------------------------------
       */
      final connection =
          DeviceConnectionService(
        baseUrl: api.baseUrl,
        deviceId: id,
      );

      /*
       * IMPORTANT:
       *
       * The connection only transports messages.
       *
       * It does not know about plugins.
       *
       * All application messages are forwarded to PaiApp.
       */
      connection.setMessageHandler(
        (message) async {
          await _handleDeviceMessage(
            message,
            commandHandler,
          );
        },
      );

      /*
       * ------------------------------------------------------------
       * 6. Create frontend PluginManager
       * ------------------------------------------------------------
       *
       * This manager controls the plugin state shown by the UI
       * and communicates with the backend REST API.
       */
      final manager =
          PluginManager(
        service: PluginService(api),
        deviceId: id,
      );

      /*
       * ------------------------------------------------------------
       * 7. Load plugin catalog
       * ------------------------------------------------------------
       */
      await manager.loadCatalog(
        platform: device.platform,
        architecture: device.architecture,
      );

      /*
       * ------------------------------------------------------------
       * 8. Store state BEFORE connecting WebSocket
       * ------------------------------------------------------------
       *
       * This guarantees that if the backend immediately sends a
       * command after connection, the application infrastructure
       * already exists.
       */
      if (!mounted) {
        connection.dispose();
        return;
      }

      setState(() {
        deviceId = id;

        pluginManager = manager;

        pluginRegistry = registry;

        pluginCommandHandler =
            commandHandler;

        connectionService =
            connection;
      });

      /*
       * ------------------------------------------------------------
       * 9. Connect WebSocket
       * ------------------------------------------------------------
       */
      await connection.connect();

      print(
        '[PaiApp] Device connection established: $id',
      );

      /*
       * ------------------------------------------------------------
       * 10. Initialization complete
       * ------------------------------------------------------------
       */
      print(
        '[PaiApp] Initialization complete',
      );
    } catch (e, stackTrace) {
      print(
        '[PaiApp] Initialization failed: $e',
      );

      print(stackTrace);

      if (!mounted) {
        return;
      }

      ScaffoldMessenger.of(context)
          .showSnackBar(
        SnackBar(
          content: Text(
            'Failed to connect to PAI: $e',
          ),
        ),
      );
    }
  }

  // ==============================================================
  // DEVICE MESSAGE ROUTING
  // ==============================================================

  Future<void> _handleDeviceMessage(
    Map<String, dynamic> message,
    PluginCommandHandler commandHandler,
  ) async {
    print(
      '[PaiApp] Device message: $message',
    );

    final type =
        message['type']?.toString();

    /*
     * ------------------------------------------------------------
     * Plugin commands
     * ------------------------------------------------------------
     */
    switch (type) {
      case 'plugin.install':
      case 'plugin.enable':
      case 'plugin.disable':
      case 'plugin.execute':
        await _handlePluginCommand(
          message,
          commandHandler,
        );
        return;

      /*
       * ----------------------------------------------------------
       * Device connection confirmation
       * ----------------------------------------------------------
       */
      case 'connected':
        print(
          '[PaiApp] Backend connection confirmed',
        );
        return;

      /*
       * ----------------------------------------------------------
       * Heartbeat acknowledgement
       * ----------------------------------------------------------
       */
      case 'heartbeat_ack':
        return;

      /*
       * ----------------------------------------------------------
       * Generic command
       * ----------------------------------------------------------
       */
      case 'command':
        await _handleCommand(
          message,
        );
        return;

      /*
       * ----------------------------------------------------------
       * Generic event
       * ----------------------------------------------------------
       */
      case 'event':
        await _handleEvent(
          message,
        );
        return;

      /*
       * ----------------------------------------------------------
       * Result generated by another subsystem
       * ----------------------------------------------------------
       */
      case 'result':
        await _handleResult(
          message,
        );
        return;

      /*
       * ----------------------------------------------------------
       * Unknown
       * ----------------------------------------------------------
       */
      default:
        print(
          '[PaiApp] Unknown message type: $type',
        );
    }
  }

  // ==============================================================
  // PLUGIN COMMAND HANDLING
  // ==============================================================

  Future<void> _handlePluginCommand(
    Map<String, dynamic> message,
    PluginCommandHandler commandHandler,
  ) async {
    final requestId =
        message['request_id']?.toString();

    final type =
        message['type']?.toString();

    print(
      '[PaiApp] Plugin command: '
      '$type '
      'request=$requestId',
    );

    try {
      /*
       * ----------------------------------------------------------
       * Execute plugin command
       * ----------------------------------------------------------
       */
      final response =
          await commandHandler.handle(
        message,
      );

      final success =
          response['success'] == true;

      /*
       * ----------------------------------------------------------
       * Send result back to backend
       * ----------------------------------------------------------
       */
      final connection =
          connectionService;

      if (connection == null) {
        print(
          '[PaiApp] Cannot send plugin result: '
          'connection is null',
        );

        return;
      }

      await connection.sendResult(
        requestId:
            requestId ?? 'unknown',
        success:
            success,
        result:
            response['result'],
        error:
            response['error']?.toString(),
      );

      /*
       * ----------------------------------------------------------
       * Update local plugin state
       * ----------------------------------------------------------
       */
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
    } catch (e, stackTrace) {
      /*
       * ----------------------------------------------------------
       * Unexpected command handling failure
       * ----------------------------------------------------------
       */
      print(
        '[PaiApp] Plugin command failed: $e',
      );

      print(stackTrace);

      /*
       * Try to notify backend.
       */
      try {
        await connectionService?.sendResult(
          requestId:
              requestId ?? 'unknown',
          success: false,
          result: {
            'operation': type,
          },
          error: e.toString(),
        );
      } catch (sendError) {
        print(
          '[PaiApp] Failed to send plugin error: '
          '$sendError',
        );
      }
    }
  }

  // ==============================================================
  // GENERIC COMMAND
  // ==============================================================

  Future<void> _handleCommand(
    Map<String, dynamic> message,
  ) async {
    print(
      '[PaiApp] Command received: $message',
    );

    /*
     * Future:
     *
     * TaskCommandRouter
     * DeviceCommandRouter
     * ExecutorRouter
     */
  }

  // ==============================================================
  // EVENT
  // ==============================================================

  Future<void> _handleEvent(
    Map<String, dynamic> message,
  ) async {
    print(
      '[PaiApp] Event received: $message',
    );

    /*
     * Future:
     *
     * EventBus
     * NotificationManager
     * TaskManager
     */
  }

  // ==============================================================
  // RESULT
  // ==============================================================

  Future<void> _handleResult(
    Map<String, dynamic> message,
  ) async {
    print(
      '[PaiApp] Result received: $message',
    );

    /*
     * Generic result handling.
     *
     * Plugin command results generated locally are already handled
     * by _handlePluginCommand().
     */
  }

  // ==============================================================
  // DISPOSE
  // ==============================================================

  @override
  void dispose() {
    print(
      '[PaiApp] Disposing application',
    );

    connectionService?.dispose();

    super.dispose();
  }

  // ==============================================================
  // UI
  // ==============================================================

  @override
  Widget build(
    BuildContext context,
  ) {
    /*
     * ------------------------------------------------------------
     * Loading state
     * ------------------------------------------------------------
     */
    if (deviceId == null ||
        pluginManager == null ||
        connectionService == null) {
      return MaterialApp(
        debugShowCheckedModeBanner: false,
        home: Scaffold(
          body: Center(
            child: Column(
              mainAxisAlignment:
                  MainAxisAlignment.center,
              children: const [
                CircularProgressIndicator(),

                SizedBox(
                  height: 16,
                ),

                Text(
                  'Connecting to PAI...',
                ),
              ],
            ),
          ),
        ),
      );
    }

    /*
     * ------------------------------------------------------------
     * Application screens
     * ------------------------------------------------------------
     */
    final screens = [
      ChatScreen(
        service:
            ChatService(api),
        deviceId:
            deviceId!,
      ),

      TasksScreen(
        service:
            TaskService(api),
      ),

      PluginsScreen(
        manager:
            pluginManager!,
      ),
    ];

    /*
     * ------------------------------------------------------------
     * Main application
     * ------------------------------------------------------------
     */
    return MaterialApp(
      debugShowCheckedModeBanner: false,

      theme: ThemeData(
        colorScheme:
            ColorScheme.fromSeed(
          seedColor:
              Colors.blue,
        ),
        useMaterial3: true,
      ),

      home: Scaffold(
        body:
            screens[page],

        bottomNavigationBar:
            NavigationBar(
          selectedIndex:
              page,

          onDestinationSelected:
              (index) {
            setState(() {
              page = index;
            });
          },

          destinations: const [
            NavigationDestination(
              icon:
                  Icon(Icons.chat),
              label:
                  'Chat',
            ),

            NavigationDestination(
              icon:
                  Icon(Icons.task),
              label:
                  'Tasks',
            ),

            NavigationDestination(
              icon:
                  Icon(Icons.extension),
              label:
                  'Plugins',
            ),
          ],
        ),
      ),
    );
  }
}

// ================================================================
// GLOBAL SCAFFOLD MESSENGER
// ================================================================

final GlobalKey<ScaffoldMessengerState>
    scaffoldMessengerKey =
    GlobalKey<ScaffoldMessengerState>();

// ================================================================
// LEGACY ROOT APPLICATION
// ================================================================
//
// Keep this only if another part of your application still uses
// MyApp. If main.dart directly uses PaiApp, you can remove this
// class and the HomeScreen import.
// ================================================================

class MyApp extends StatelessWidget {
  const MyApp({
    super.key,
  });

  @override
  Widget build(
    BuildContext context,
  ) {
    return MaterialApp(
      title:
          'Personal AI',

      scaffoldMessengerKey:
          scaffoldMessengerKey,

      theme:
          ThemeData(
        colorScheme:
            ColorScheme.fromSeed(
          seedColor:
              Colors.deepPurple,
        ),
      ),

      home:
          const HomeScreen(),

      debugShowCheckedModeBanner:
          false,
    );
  }
}