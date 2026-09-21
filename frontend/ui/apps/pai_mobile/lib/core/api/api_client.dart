import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../config/app_config.dart';


class ApiClient {
  final String baseUrl;

  ApiClient({
    String? baseUrl,
  }) : baseUrl = baseUrl ?? AppConfig.baseUrl;


  Future<dynamic> get(
    String path, {
    Map<String, String>? query,
  }) async {
    final uri = Uri.parse('$baseUrl$path').replace(
      queryParameters: query,
    );

    final response = await http.get(uri);

    return _handle(response);
  }


  Future<dynamic> post(
    String path,
    Map<String, dynamic> body,
  ) async {
    final response = await http.post(
      Uri.parse('$baseUrl$path'),
      headers: {
        'Content-Type': 'application/json',
      },
      body: jsonEncode(body),
    );

    return _handle(response);
  }


  Future<dynamic> put(
    String path,
    Map<String, dynamic> body,
  ) async {
    final response = await http.put(
      Uri.parse('$baseUrl$path'),
      headers: {
        'Content-Type': 'application/json',
      },
      body: jsonEncode(body),
    );

    return _handle(response);
  }


  dynamic _handle(http.Response response) {
    final data = response.body.isEmpty
        ? null
        : jsonDecode(response.body);

    if (response.statusCode >= 200 &&
        response.statusCode < 300) {
      return data;
    }

    throw Exception(
      'API error ${response.statusCode}: $data',
    );
  }
}