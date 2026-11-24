import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// One Server-Sent Event.
class SseEvent {
  final String event; // 'message' when the stream gave no `event:` line
  final String data; // multi-line `data:` joined with '\n'
  final String? id;
  const SseEvent({required this.event, required this.data, this.id});

  @override
  String toString() => 'SseEvent($event, $data)';
}

/// Incremental SSE parser (WHATWG "event stream" format).
///
/// Feed it decoded text in arbitrary pieces — chunk boundaries may fall inside a
/// line, between `\r` and `\n`, or between events. Comment lines (`: heartbeat`)
/// are ignored; an event is dispatched at each blank line.
class SseParser {
  String _buf = '';
  String _event = '';
  final List<String> _data = [];
  String? _id;
  bool _pendingCr = false;

  List<SseEvent> add(String chunk) {
    final out = <SseEvent>[];
    var text = chunk;
    if (_pendingCr) {
      _pendingCr = false;
      if (text.startsWith('\n')) text = text.substring(1);
    }
    _buf += text;

    // Normalise newlines: \r\n and lone \r both end a line.
    while (true) {
      final i = _buf.indexOf(RegExp(r'[\r\n]'));
      if (i < 0) break;
      final isCr = _buf[i] == '\r';
      if (isCr && i == _buf.length - 1) {
        // Could be the first half of \r\n: finish the line now, swallow a following \n.
        final line = _buf.substring(0, i);
        _buf = '';
        _pendingCr = true;
        _line(line, out);
        break;
      }
      final line = _buf.substring(0, i);
      var next = i + 1;
      if (isCr && _buf[next] == '\n') next++;
      _buf = _buf.substring(next);
      _line(line, out);
    }
    return out;
  }

  void _line(String line, List<SseEvent> out) {
    if (line.isEmpty) {
      if (_data.isNotEmpty || _event.isNotEmpty) {
        out.add(SseEvent(
          event: _event.isEmpty ? 'message' : _event,
          data: _data.join('\n'),
          id: _id,
        ));
      }
      _event = '';
      _data.clear();
      return;
    }
    if (line.startsWith(':')) return; // comment / heartbeat
    final colon = line.indexOf(':');
    final field = colon < 0 ? line : line.substring(0, colon);
    var value = colon < 0 ? '' : line.substring(colon + 1);
    if (value.startsWith(' ')) value = value.substring(1);
    switch (field) {
      case 'event':
        _event = value;
      case 'data':
        _data.add(value);
      case 'id':
        _id = value;
    }
  }
}

/// Turns a byte stream into [SseEvent]s (handles multi-byte UTF-8 split across chunks).
Stream<SseEvent> parseSse(Stream<List<int>> bytes) {
  final parser = SseParser();
  return bytes
      .transform(utf8.decoder)
      .expand(parser.add);
}

class SseHttpException implements Exception {
  final int statusCode;
  final String body;
  SseHttpException(this.statusCode, this.body);
  @override
  String toString() => 'SSE request failed ($statusCode): $body';
}

/// Opens `text/event-stream` responses (POST for chat replies, GET for long-lived feeds).
class SseClient {
  final http.Client Function() _clientFactory;
  SseClient({http.Client Function()? clientFactory})
      : _clientFactory = clientFactory ?? http.Client.new;

  /// POSTs JSON and streams the response.
  Stream<SseEvent> post(
    Uri url, {
    required Map<String, String> headers,
    required Object body,
  }) {
    final req = http.Request('POST', url)
      ..headers.addAll({
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'text/event-stream',
        'Cache-Control': 'no-cache',
        ...headers,
      })
      ..body = jsonEncode(body);
    return _stream(req);
  }

  /// GETs and streams the response (e.g. the per-user task feed). `headers` may carry
  /// `Last-Event-ID` to resume. The stream headers always win over the caller's: the shared
  /// ApiClient headers say `Accept: application/json`, which would be wrong here.
  ///
  /// [onOpen] fires once the server has accepted the connection (HTTP 200), before any event:
  /// a resumed feed can stay silent for a while, so events alone can't tell "connected".
  Stream<SseEvent> get(Uri url, {required Map<String, String> headers, void Function()? onOpen}) {
    final req = http.Request('GET', url)
      ..headers.addAll({
        ...headers,
        'Accept': 'text/event-stream',
        'Cache-Control': 'no-cache',
      })
      ..headers.remove('Content-Type');
    return _stream(req, onOpen: onOpen);
  }

  Stream<SseEvent> _stream(http.Request req, {void Function()? onOpen}) async* {
    final client = _clientFactory();
    try {
      final resp = await client.send(req);
      if (resp.statusCode != 200) {
        final text = await resp.stream.bytesToString();
        throw SseHttpException(resp.statusCode, text);
      }
      onOpen?.call();
      yield* parseSse(resp.stream);
    } finally {
      // Cancelling the listener (or leaving the loop) closes the connection, which
      // tells the server to stop generating / to drop this subscriber.
      client.close();
    }
  }
}
