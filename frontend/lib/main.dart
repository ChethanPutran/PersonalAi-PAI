import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'app.dart';
import 'config/app_config.dart';


void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await dotenv.load();
  await AppConfig.loadSavedServer();      // the server address the user chose in the app, if any

   try {
    await Firebase.initializeApp();
  } catch (_) {
    // Allows the app to boot without firebase config.
  }
  runApp(const PaiApp());
}


