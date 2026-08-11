import 'package:flutter_local_notifications/flutter_local_notifications.dart';

class NotificationService {
  static final FlutterLocalNotificationsPlugin
      _notifications =
      FlutterLocalNotificationsPlugin();

  static Future<void> initialize() async {
    const android =
        AndroidInitializationSettings(
      '@mipmap/ic_launcher',
    );

    const ios =
        DarwinInitializationSettings();

    const settings = InitializationSettings(
      android: android,
      iOS: ios,
    );

    await _notifications.initialize(
      settings: settings,
    );

    await _notifications
        .resolvePlatformSpecificImplementation<
            IOSFlutterLocalNotificationsPlugin>()
        ?.requestPermissions(
          alert: true,
          badge: true,
          sound: true,
        );
  }

  static Future<void> showNotification(
    String title,
    String body,
  ) async {
    const androidDetails =
        AndroidNotificationDetails(
      'pai_channel',
      'Personal AI Notifications',
      channelDescription:
          'Notifications from Personal AI',
      importance: Importance.high,
      priority: Priority.high,
    );

    const iosDetails =
        DarwinNotificationDetails();

    const details = NotificationDetails(
      android: androidDetails,
      iOS: iosDetails,
    );

    final int id =
        DateTime.now().millisecondsSinceEpoch ~/
            1000;

    await _notifications.show(
      id: id,
      title: title,
      body: body,
      notificationDetails: details,
    );
  }
}