import 'dart:async';
import 'dart:io' show Platform;

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../core/api/api_client.dart';

/// Top-level background handler. Firebase requires this to be a
/// top-level function — cannot be a closure or method.
@pragma('vm:entry-point')
Future<void> firebaseBackgroundHandler(RemoteMessage message) async {
  await Firebase.initializeApp();
  // System tray notification is auto-shown by FCM when `notification`
  // is present. Nothing to do here.
}

class PushService {
  static final _localNotif = FlutterLocalNotificationsPlugin();

  /// Called when the user taps a notification (foreground, background,
  /// or app-launch). Set by PaiApp to switch to the Reminders tab.
  static void Function(Map<String, dynamic> data)? onNotificationTap;

  static bool _initialized = false;
  static String? _currentToken;
  static String? _currentDeviceId;
  static ApiClient? _api;

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  static Future<void> init({required ApiClient api}) async {
    if (_initialized) return;

    // FCM supports Android, iOS, Web, macOS. Not Linux/Windows.
    if (!Platform.isAndroid && !Platform.isIOS && !Platform.isMacOS) {
      debugPrint('[Push] Skipped — platform ${Platform.operatingSystem} '
          'does not support FCM');
      return;
    }

    _api = api;

    // Firebase — no-op if google-services.json is missing.
    try {
      await Firebase.initializeApp();
    } catch (e) {
      debugPrint('[Push] Firebase init failed (push disabled): $e');
      return;
    }

    // Local notifications for foreground display.
    const androidInit =
        AndroidInitializationSettings('@mipmap/ic_launcher');
    const iosInit = DarwinInitializationSettings(
      requestAlertPermission: true,
      requestSoundPermission: true,
      requestBadgePermission: true,
    );
    const linux = LinuxInitializationSettings(
      defaultActionName: 'Open notification',
    );
    await _localNotif.initialize(
      settings: const InitializationSettings(android: androidInit, iOS: iosInit, linux: linux),
      onDidReceiveNotificationResponse: (resp) {
        final payload = resp.payload;
        if (payload == null) return;
        // Payload is a simple `type|schedule_id|task_id` string.
        final parts = payload.split('|');
        if (parts.length >= 3 && parts[0] == 'schedule_reminder') {
          onNotificationTap?.call({
            'type': parts[0],
            'schedule_id': parts[1],
            'task_id': parts[2],
          });
        }
      },
    );

    FirebaseMessaging.onBackgroundMessage(firebaseBackgroundHandler);

    // Foreground: FCM does NOT show system notifications on Android/iOS
    // foreground by default — show them ourselves.
    FirebaseMessaging.onMessage.listen((msg) async {
      final n = msg.notification;
      if (n == null) return;
      await _localNotif.show(
        id: (msg.messageId ?? '').hashCode & 0x7fffffff,
        title: n.title,
        body: n.body,
        notificationDetails: const NotificationDetails(
          android: AndroidNotificationDetails(
            'pai_reminders',
            'PAI Reminders',
            channelDescription: 'Scheduled reminders',
            importance: Importance.max,
            priority: Priority.high,
          ),
          iOS: DarwinNotificationDetails(),
          linux: LinuxNotificationDetails(),
        ),
        payload: 'schedule_reminder|'
            '${msg.data['schedule_id'] ?? ''}|'
            '${msg.data['task_id'] ?? ''}',
      );
    });

    // Background tap.
    FirebaseMessaging.onMessageOpenedApp.listen(_handleTap);

    // App launched from a notification tap (cold start).
    final initial = await FirebaseMessaging.instance.getInitialMessage();
    if (initial != null) _handleTap(initial);

    // Permissions (iOS + Android 13+).
    final settings = await FirebaseMessaging.instance.requestPermission(
      alert: true, badge: true, sound: true,
    );
    if (settings.authorizationStatus == AuthorizationStatus.denied) {
      debugPrint('[Push] Notification permission denied');
      return;
    }

    // Token management.
    _currentToken = await FirebaseMessaging.instance.getToken();
    debugPrint('[Push] token=$_currentToken');

    FirebaseMessaging.instance.onTokenRefresh.listen((t) {
      _currentToken = t;
      _uploadTokenIfReady();
    });

    _initialized = true;

    // If a device id was already known, upload now.
    _uploadTokenIfReady();
  }

  static void _handleTap(RemoteMessage msg) {
    if (msg.data['type'] == 'schedule_reminder') {
      onNotificationTap?.call(Map<String, dynamic>.from(msg.data));
    }
  }

  // ------------------------------------------------------------------
  // Upload token to backend
  // ------------------------------------------------------------------

  /// PaiApp calls this after device registration and after WS connect.
  static Future<void> registerDevice(String deviceId) async {
    _currentDeviceId = deviceId;
    await _uploadTokenIfReady();
  }

  /// On logout.
  static Future<void> unregisterDevice() async {
    final api = _api;
    final dev = _currentDeviceId;
    final tok = _currentToken;
    if (api == null || dev == null || tok == null) return;
    try {
      await api.delete(
        '/devices/$dev/push-token?token=${Uri.encodeComponent(tok)}',
      );
    } catch (e) {
      debugPrint('[Push] unregister failed: $e');
    }
    _currentDeviceId = null;
  }

  static String? _platformName() {
  if (Platform.isAndroid) return 'android';
  if (Platform.isIOS) return 'ios';
  // Extend PushTokenIn platform pattern if you ever support these.
  return null;
}

  static Future<void> _uploadTokenIfReady() async {
  final api = _api;
  final dev = _currentDeviceId;
  final tok = _currentToken;
  final platform = _platformName();
  if (!_initialized || api == null || dev == null || tok == null
      || platform == null) return;

  try {
    await api.post('/devices/$dev/push-token', {
      'token': tok,
      'platform': platform,
    });
    debugPrint('[Push] token registered for device=$dev');
  } catch (e) {
    debugPrint('[Push] token upload failed: $e');
  }
}

}

