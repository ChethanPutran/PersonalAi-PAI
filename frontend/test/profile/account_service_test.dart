import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/core/api/api_client.dart';
import 'package:pai_mobile/profile/account_service.dart';

class _Api extends ApiClient {
  _Api() : super(baseUrl: 'http://test', apiPrefix: '/api/v1');
  final calls = <String>[];
  final bodies = <Map<String, dynamic>>[];
  Object? reply;
  Object? fail;

  Future<dynamic> _do(String verb, String path, [Map<String, dynamic>? body]) async {
    calls.add('$verb $path');
    if (body != null) bodies.add(body);
    if (fail != null) throw fail!;
    return reply;
  }

  @override
  Future<dynamic> get(String path, {Map<String, String>? query}) => _do('GET', path);
  @override
  Future<dynamic> patch(String path, Map<String, dynamic> body) => _do('PATCH', path, body);
  @override
  Future<dynamic> put(String path, Map<String, dynamic> body) => _do('PUT', path, body);
}

void main() {
  test('getProfile reads the account fields', () async {
    final api = _Api()
      ..reply = {'id': 'u1', 'email': 'a@b.c', 'display_name': 'Chethan', 'timezone': 'Asia/Kolkata', 'locale': 'en-IN', 'created_at': '2026-03-04T10:00:00+00:00'};
    final p = await AccountService(api: api).getProfile();
    expect(api.calls, ['GET /account/profile']);
    expect((p.displayName, p.timezone, p.locale), ('Chethan', 'Asia/Kolkata', 'en-IN'));
    expect(p.createdAt, isNotNull);
  });

  test('updateProfile sends only what is given (trimmed); an empty string clears', () async {
    final api = _Api()..reply = {'id': 'u1', 'email': 'a@b.c'};
    await AccountService(api: api).updateProfile(displayName: '  Chethan R  ', locale: '');
    expect(api.calls, ['PATCH /account/profile']);
    expect(api.bodies.single, {'display_name': 'Chethan R', 'locale': ''});
  });

  test('personalization: defaults for missing fields, unknown tone/detail fall back', () async {
    final api = _Api()..reply = {'likes': ['jazz'], 'tone': 'sarcastic'};
    final p = await AccountService(api: api).getPersonalization();
    expect(api.calls, ['GET /account/personalization']);
    expect(p.likes, ['jazz']);
    expect(p.dislikes, isEmpty);
    expect((p.tone, p.detail), ('friendly', 'balanced'));
    expect(p.isBlank, isFalse);
    expect(const Personalization().isBlank, isTrue);
  });

  test('savePersonalization PUTs the whole shape the server expects', () async {
    final api = _Api()..reply = {'preferred_name': 'Chet', 'tone': 'concise', 'detail': 'brief', 'hobbies': ['chess']};
    final saved = await AccountService(api: api).savePersonalization(const Personalization(
      preferredName: 'Chet', about: 'ML researcher', occupation: 'PhD student', likes: ['jazz'], dislikes: ['noise'],
      interests: ['space'], hobbies: ['chess'], tone: 'concise', detail: 'brief'));
    expect(api.calls, ['PUT /account/personalization']);
    expect(api.bodies.single.keys.toSet(),
        {'preferred_name', 'about', 'occupation', 'likes', 'dislikes', 'interests', 'hobbies', 'tone', 'detail'});
    expect(api.bodies.single['tone'], 'concise');
    expect(saved.hobbies, ['chess']);
  });

  test('renameDevice PATCHes the name and returns what the server stored (wrapped or not)', () async {
    final api = _Api()..reply = {'device': {'id': 'd1', 'name': 'Work phone'}};
    expect(await AccountService(api: api).renameDevice('d1', '  Work phone '), 'Work phone');
    expect(api.calls, ['PATCH /devices/d1']);
    expect(api.bodies.single, {'name': 'Work phone'});
    api.reply = {'id': 'd1', 'name': 'Plain'};
    expect(await AccountService(api: api).renameDevice('d1', 'Plain'), 'Plain');
  });

  test('Personalization equality ignores identity, not content', () {
    expect(const Personalization(likes: ['a']) == const Personalization(likes: ['a']), isTrue);
    expect(const Personalization(likes: ['a']) == const Personalization(likes: ['b']), isFalse);
    expect(const Personalization(tone: 'formal') == const Personalization(), isFalse);
  });

  group('accountErrorMessage', () {
    test('a plain detail', () => expect(accountErrorMessage(ApiException(422, {'detail': 'Unknown time zone'})), 'Unknown time zone'));
    test('a detail object with a message', () => expect(accountErrorMessage(ApiException(403, {'detail': {'code': 'x', 'message': 'Not yours.'}})), 'Not yours.'));
    test('FastAPI validation errors become sentences', () {
      final e = ApiException(422, {'detail': [{'msg': 'Value error, Language must look like en'}, {'msg': 'too long'}]});
      expect(accountErrorMessage(e), 'Language must look like en. too long');
    });
    test('anything else is a plain hint', () {
      expect(accountErrorMessage(Exception('socket')), contains('Check your connection'));
      expect(accountErrorMessage(ApiException(500, 'x')), contains('500'));
    });
  });
}
