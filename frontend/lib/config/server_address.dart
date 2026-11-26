import 'dart:async';

import 'package:http/http.dart' as http;

/// The PAI server address the user typed, checked and cleaned.
///
/// Accepts `192.168.1.20`, `192.168.1.20:8000`, `pai.example.com`, `https://pai.example.com`,
/// `http://localhost:8000/` and the like. Rejects empty text, text with spaces, a path, a query, another
/// scheme and an out of range port. The result [url] is host-only (`scheme://host[:port]`, no trailing slash).
class ServerAddress {
  final String scheme;
  final String host;
  final int port;

  /// Whether the user typed the port (otherwise [defaultPort] was filled in).
  final bool portGiven;

  /// A plain-words warning to show before saving (for example plain http to a host outside the home network).
  final String? warning;

  const ServerAddress._(this.scheme, this.host, this.port, this.portGiven, this.warning);

  static const int defaultPort = 8000;

  /// `http://host:port`; a port that is the scheme's own default (80 / 443) is left out.
  String get url {
    final h = host.contains(':') ? '[$host]' : host;     // IPv6 literal
    final own = (scheme == 'http' && port == 80) || (scheme == 'https' && port == 443);
    return own ? '$scheme://$h' : '$scheme://$h:$port';
  }

  @override
  String toString() => url;

  /// Parses [input]. On failure the result has [ServerAddressResult.error] set (plain words, no jargon).
  static ServerAddressResult parse(String? input) {
    var s = (input ?? '').trim();
    if (s.isEmpty) return const ServerAddressResult.error('Enter the server address, for example 192.168.1.20 or pai.example.com.');
    if (RegExp(r'\s').hasMatch(s)) return const ServerAddressResult.error('The address cannot contain spaces.');

    var scheme = 'http';
    var schemeGiven = false;
    final m = RegExp(r'^([a-zA-Z][a-zA-Z0-9+.\-]*)://(.*)$').firstMatch(s);
    if (m != null) {
      final sch = m.group(1)!.toLowerCase();
      if (sch != 'http' && sch != 'https') {
        return const ServerAddressResult.error('Use http or https (for example https://pai.example.com).');
      }
      scheme = sch;
      schemeGiven = true;
      s = m.group(2)!;
    }

    while (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    if (s.contains('/') || s.contains('?') || s.contains('#') || s.contains('@')) {
      return const ServerAddressResult.error('Enter only the address (and port), without a path. The app adds /api/v1 itself.');
    }
    if (s.isEmpty) return const ServerAddressResult.error('Enter the server address, for example 192.168.1.20 or pai.example.com.');

    String host;
    String? portText;
    if (s.startsWith('[')) {                                     // [::1]:8000
      final end = s.indexOf(']');
      if (end < 0) return const ServerAddressResult.error('That address is not valid.');
      host = s.substring(1, end);
      final rest = s.substring(end + 1);
      if (rest.isNotEmpty) {
        if (!rest.startsWith(':')) return const ServerAddressResult.error('That address is not valid.');
        portText = rest.substring(1);
      }
      if (!RegExp(r'^[0-9a-fA-F:.]+$').hasMatch(host) || !host.contains(':')) {
        return const ServerAddressResult.error('That address is not valid.');
      }
    } else {
      final colon = s.indexOf(':');
      if (colon >= 0) {
        if (s.indexOf(':', colon + 1) >= 0) return const ServerAddressResult.error('Put an IPv6 address in square brackets, like [::1]:8000.');
        host = s.substring(0, colon);
        portText = s.substring(colon + 1);
      } else {
        host = s;
      }
      host = host.toLowerCase();
      if (!_validHost(host)) return const ServerAddressResult.error('That address is not valid. Use a name like pai.example.com or an IP like 192.168.1.20.');
    }

    int? port;
    if (portText != null) {
      if (!RegExp(r'^[0-9]{1,5}$').hasMatch(portText)) return const ServerAddressResult.error('The port must be a number between 1 and 65535.');
      port = int.parse(portText);
      if (port < 1 || port > 65535) return const ServerAddressResult.error('The port must be a number between 1 and 65535.');
    }
    // A bare https host means 443; otherwise the PAI default.
    final usePort = port ?? (scheme == 'https' && schemeGiven ? 443 : defaultPort);

    String? warning;
    if (scheme == 'http' && !isPrivateHost(host)) {
      warning = 'This is plain http to a server outside your home network: anything you send, including your password, '
          'can be read on the way. Use https if you can.';
    }
    return ServerAddressResult.ok(ServerAddress._(scheme, host, usePort, port != null, warning));
  }

  static bool _validHost(String h) {
    if (h.isEmpty || h.length > 253) return false;
    if (RegExp(r'^[0-9.]+$').hasMatch(h)) return _ipv4(h) != null;            // looks like an IP: must be one
    return RegExp(r'^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)*$').hasMatch(h);
  }

  static List<int>? _ipv4(String h) {
    final parts = h.split('.');
    if (parts.length != 4) return null;
    final out = <int>[];
    for (final p in parts) {
      if (!RegExp(r'^[0-9]{1,3}$').hasMatch(p)) return null;
      final n = int.parse(p);
      if (n > 255) return null;
      out.add(n);
    }
    return out;
  }

  /// A host that is on this machine or the home/office network, where plain http is normal:
  /// localhost, the Android emulator's 10.0.2.2, loopback, private IPv4 ranges, link-local, `.local` / `.lan`
  /// names and single-label names (a hostname with no dot is a LAN name).
  static bool isPrivateHost(String host) {
    final h = host.toLowerCase();
    if (h == 'localhost' || h.endsWith('.localhost') || h.endsWith('.local') || h.endsWith('.lan') || h.endsWith('.home.arpa')) return true;
    if (h.contains(':')) return h == '::1' || h.startsWith('fe80:') || h.startsWith('fc') || h.startsWith('fd');
    final ip = _ipv4(h);
    if (ip != null) {
      if (ip[0] == 10 || ip[0] == 127) return true;
      if (ip[0] == 192 && ip[1] == 168) return true;
      if (ip[0] == 172 && ip[1] >= 16 && ip[1] <= 31) return true;
      if (ip[0] == 169 && ip[1] == 254) return true;
      if (ip[0] == 100 && ip[1] >= 64 && ip[1] <= 127) return true;       // CGNAT / Tailscale
      return false;
    }
    return !h.contains('.') && !h.contains(':');
  }
}

class ServerAddressResult {
  final ServerAddress? address;
  final String? error;
  const ServerAddressResult.ok(ServerAddress this.address) : error = null;
  const ServerAddressResult.error(String this.error) : address = null;
  bool get ok => address != null;
}

/// Outcome of "Test connection".
class ConnectionTest {
  final bool ok;
  final String message;
  const ConnectionTest(this.ok, this.message);
}

/// Asks `<url>/api/v1/health` (public, no sign-in needed) and says in plain words whether a PAI server answered.
Future<ConnectionTest> testServerConnection(
  String url, {
  http.Client? client,
  Duration timeout = const Duration(seconds: 6),
}) async {
  final c = client ?? http.Client();
  try {
    final res = await c.get(Uri.parse('$url/api/v1/health')).timeout(timeout);
    if (res.statusCode == 200 && res.body.contains('"status"') && res.body.toLowerCase().contains('healthy')) {
      return const ConnectionTest(true, 'Connected: a PAI server answered.');
    }
    if (res.statusCode == 200) return const ConnectionTest(false, 'Something answered, but it does not look like a PAI server.');
    return ConnectionTest(false, 'The server answered with an error (${res.statusCode}). Is this the PAI address?');
  } on TimeoutException {
    return const ConnectionTest(false, 'No answer within a few seconds. Check the address, the port and that the server is running and reachable from this phone.');
  } catch (_) {
    return const ConnectionTest(false, "Couldn't connect. Check the address and port, that the server is running, and that this phone can reach it (same Wi-Fi?).");
  } finally {
    if (client == null) c.close();
  }
}
