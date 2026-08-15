import 'package:dio/dio.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';

class FileService {
  static final Dio _dio = Dio(BaseOptions(
    baseUrl: dotenv.env['API_BASE_URL'] ?? 'http://localhost:8000',
    connectTimeout: const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 30),
  ));

  static Future<Map<String, dynamic>> listFiles(String path, {String? userId}) async {
    final qp = {'path': path};
    if (userId != null && userId.isNotEmpty) qp['user_id'] = userId;
    final response = await _dio.get('/api/v1/files/list', queryParameters: qp);
    return response.data;
  }

  static Future<Map<String, dynamic>> readFile(String path, {String? userId}) async {
    final qp = {'path': path};
    if (userId != null && userId.isNotEmpty) qp['user_id'] = userId;
    final response = await _dio.get('/api/v1/files/read', queryParameters: qp);
    return response.data;
  }

  static Future<Map<String, dynamic>> requestAccess(String userId, String path) async {
    final response = await _dio.post('/api/v1/files/request-access', data: {'user_id': userId, 'path': path});
    return response.data;
  }

  static Future<Map<String, dynamic>> listRequests({String? userId}) async {
    final response = await _dio.get('/api/v1/files/permissions/requests', queryParameters: {'user_id': userId});
    return response.data;
  }

  static Future<Map<String, dynamic>> approveRequest(int requestId) async {
    final response = await _dio.post('/api/v1/files/permissions/requests/$requestId/approve');
    return response.data;
  }

  static Future<Map<String, dynamic>> denyRequest(int requestId) async {
    final response = await _dio.post('/api/v1/files/permissions/requests/$requestId/deny');
    return response.data;
  }
}
