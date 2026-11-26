import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:pai_mobile/config/server_address.dart';

String? urlOf(String s) => ServerAddress.parse(s).address?.url;

void main() {
  group('ServerAddress.parse', () {
    test('adds http and the default port to a bare host or IP', () {
      expect(urlOf('192.168.1.20'), 'http://192.168.1.20:8000');
      expect(urlOf('pai.local'), 'http://pai.local:8000');
      expect(urlOf('localhost'), 'http://localhost:8000');
    });
    test('keeps a given port and scheme', () {
      expect(urlOf('192.168.1.20:9000'), 'http://192.168.1.20:9000');
      expect(urlOf('https://pai.example.com'), 'https://pai.example.com');
      expect(urlOf('https://pai.example.com:8443'), 'https://pai.example.com:8443');
      expect(urlOf('HTTP://Example.COM:80'), 'http://example.com');
    });
    test('trims, strips trailing slashes, lowercases the host', () {
      expect(urlOf('  http://10.0.2.2:8000//  '), 'http://10.0.2.2:8000');
      expect(urlOf('PAI.Example.com/'), 'http://pai.example.com:8000');
    });
    test('accepts IPv6 in brackets', () {
      expect(urlOf('[::1]:8000'), 'http://[::1]:8000');
      expect(urlOf('[fe80::1]'), 'http://[fe80::1]:8000');
    });
    test('rejects empty and garbage with a plain message', () {
      for (final bad in [
        '', '   ', 'http://', 'ftp://host', 'a b', 'http://host/api/v1', 'host/path', 'host?x=1', 'user@host',
        'host:', 'host:abc', 'host:0', 'host:65536', 'host:99999999', '999.1.1.1', '1.2.3', '-bad.com', 'bad_host', '::1', 'http://[::1',
      ]) {
        final r = ServerAddress.parse(bad);
        expect(r.ok, isFalse, reason: bad);
        expect(r.error, isNotEmpty, reason: bad);
      }
      expect(ServerAddress.parse(null).ok, isFalse);
    });
    test('warns about plain http outside the local network only', () {
      expect(ServerAddress.parse('http://pai.example.com').address!.warning, contains('plain http'));
      expect(ServerAddress.parse('8.8.8.8').address!.warning, isNotNull);
      for (final lan in ['192.168.1.5', '10.0.2.2', '172.16.0.9', '127.0.0.1', 'localhost', 'nas.local', 'myserver', '100.100.1.1', '169.254.1.1', '[::1]']) {
        expect(ServerAddress.parse(lan).address!.warning, isNull, reason: lan);
      }
      expect(ServerAddress.parse('172.32.0.1').address!.warning, isNotNull);      // outside 172.16/12
      expect(ServerAddress.parse('https://pai.example.com').address!.warning, isNull);
    });
  });

  group('testServerConnection', () {
    test('ok when a PAI server answers healthy', () async {
      late Uri asked;
      final c = MockClient((req) async {
        asked = req.url;
        return http.Response('{"status":"healthy","system":"Personal AI"}', 200);
      });
      final t = await testServerConnection('http://h:8000', client: c);
      expect(t.ok, isTrue);
      expect(asked.toString(), 'http://h:8000/api/v1/health');
    });
    test('says it is not PAI when something else answers', () async {
      final t = await testServerConnection('http://h', client: MockClient((_) async => http.Response('<html>', 200)));
      expect(t.ok, isFalse);
      expect(t.message, contains('PAI'));
    });
    test('reports an error status', () async {
      final t = await testServerConnection('http://h', client: MockClient((_) async => http.Response('', 404)));
      expect(t.ok, isFalse);
      expect(t.message, contains('404'));
    });
    test('reports a refused connection and a timeout in plain words', () async {
      var t = await testServerConnection('http://h', client: MockClient((_) async => throw Exception('refused')));
      expect(t.ok, isFalse);
      expect(t.message, contains("Couldn't connect"));
      t = await testServerConnection('http://h',
          client: MockClient((_) => Completer<http.Response>().future), timeout: const Duration(milliseconds: 20));
      expect(t.ok, isFalse);
      expect(t.message, contains('No answer'));
    });
  });
}
