import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:speech_to_text/speech_to_text.dart'
    as stt;

class VoiceService {
  final stt.SpeechToText _speech =
      stt.SpeechToText();

  bool _isListening = false;

  bool get isListening => _isListening;

  Future<bool> initialize() async {
    final available =
        await _speech.initialize(
      onStatus: (status) {
        debugPrint('STT status: $status');

        if (status == 'done' ||
            status == 'notListening') {
          _isListening = false;
        }
      },
      onError: (error) {
        debugPrint('STT error: $error');
        _isListening = false;
      },
    );

    return available;
  }

  Future<void> startListening(
    Function(String) onResult,
  ) async {
    if (_isListening) return;

    _isListening = true;

    await _speech.listen(
      onResult: (result) {
        onResult(result.recognizedWords);
      },
      listenFor: const Duration(seconds: 10),
      pauseFor: const Duration(seconds: 2)
    );
  }

  Future<void> stopListening() async {
    await _speech.stop();
    _isListening = false;
  }
}