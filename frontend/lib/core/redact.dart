/// Log-safe form of a URL: the `token` query value (a JWT) is replaced, nothing else changes.
String redactUrl(Object url) =>
    url.toString().replaceAllMapped(RegExp(r'([?&]token=)[^&#\s]+'), (m) => '${m[1]}<redacted>');
