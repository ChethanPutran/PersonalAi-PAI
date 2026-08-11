import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';
import 'package:pai_mobile/providers/websocket_provider.dart';

Future<void> sendLocationUpdate(
  WidgetRef ref,
) async {
  try {
    bool serviceEnabled;
    LocationPermission permission;

    serviceEnabled =
        await Geolocator.isLocationServiceEnabled();

    if (!serviceEnabled) {
      debugPrint('Location services disabled');
      return;
    }

    permission =
        await Geolocator.checkPermission();

    if (permission == LocationPermission.denied) {
      permission =
          await Geolocator.requestPermission();

      if (permission == LocationPermission.denied) {
        debugPrint('Location permission denied');
        return;
      }
    }

    if (permission ==
        LocationPermission.deniedForever) {
      debugPrint('Location permanently denied');
      return;
    }

    final pos =
        await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.high,
      ),
    );

    final ws = ref.read(websocketProvider);

    ws.sendGoal(
      "Update location",
      context: {
        'latitude': pos.latitude,
        'longitude': pos.longitude,
        'timestamp':
            DateTime.now().toIso8601String(),
      },
    );
  } catch (e) {
    debugPrint('Location error: $e');
  }
}