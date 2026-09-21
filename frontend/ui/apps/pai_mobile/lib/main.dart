import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
// import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'services/notification_service.dart';
import 'app.dart';


void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await dotenv.load();
  await NotificationService.initialize();

  // runApp(const ProviderScope(child: MyApp()));
  runApp(const PaiApp());
}


