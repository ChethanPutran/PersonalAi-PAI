import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'app.dart';


void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await dotenv.load();

   try {
    await Firebase.initializeApp();
  } catch (_) {
    // Allows the app to boot without firebase config.
  }
  runApp(const PaiApp());
}


