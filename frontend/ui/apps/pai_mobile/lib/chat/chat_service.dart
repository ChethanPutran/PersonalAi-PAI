  import '../core/api/api_client.dart';


class ChatService {
  final ApiClient api;

  ChatService(this.api);


  Future<String> sendMessage({
    required String deviceId,
    required String message,
    String mode = 'text',
  }) async {
    final result = await api.post(
      '/chat',
      {
        'device_id': deviceId,
        'message': message,
        'mode': mode,
      },
    );

    return result['message'];
  }
}