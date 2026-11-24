// lib/notifications/notification_service.dart
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

class NotificationService {
  static final _plugin = FlutterLocalNotificationsPlugin();
  static bool _ready = false;

  static Future<void> init() async {
    if (_ready) return;
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    const ios = DarwinInitializationSettings(
      requestAlertPermission: true,
      requestSoundPermission: true,
      requestBadgePermission: true,
    );
    const linux = LinuxInitializationSettings(
      defaultActionName: 'Open',
    );
    await _plugin.initialize(
      settings: const InitializationSettings(android: android, iOS: ios, linux: linux),
    );
    // Ask permission on Android 13+
    await _plugin
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();
    _ready = true;
  }

  static Future<void> show({
    required String title,
    required String body,
    int id = 0,
  }) async {
    await init();
    const details = NotificationDetails(
      android: AndroidNotificationDetails(
        'pai_reminders',
        'PAI Reminders',
        channelDescription: 'Scheduled reminders from PAI',
        importance: Importance.max,
        priority: Priority.high,
      ),
      iOS: DarwinNotificationDetails(),
      linux: LinuxNotificationDetails(
        urgency: LinuxNotificationUrgency.normal,
      ),
    );
    await _plugin.show(
      id: id,
      title: title,
      body: body,
      notificationDetails: details,
    );
  }
}