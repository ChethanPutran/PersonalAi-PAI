class AppConfig {
  static const String baseUrl = String.fromEnvironment(
    'PAI_SERVER_URL',
    defaultValue: 'http://127.0.0.1:8000/api/v1',
  );
  
}