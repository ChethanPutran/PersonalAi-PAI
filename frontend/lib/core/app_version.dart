/// The version of THIS app build. Keep in sync with `version:` in pubspec.yaml (the part
/// before `+`). Reported to the backend at device registration, where it gates which
/// plugin versions the device is offered (a manifest's `runtime.minAppVersion`).
const String kAppVersion = '1.0.0';

/// Parsed `major.minor.patch` (extra segments ignored; `+build` / `-pre` suffixes stripped).
List<int>? _parts(String? v) {
  if (v == null) return null;
  final core = v.trim().replaceFirst(RegExp(r'^[vV]'), '').split(RegExp(r'[+-]')).first;
  if (core.isEmpty) return null;
  final out = <int>[];
  for (final s in core.split('.')) {
    final n = int.tryParse(s);
    if (n == null) return null;
    out.add(n);
  }
  return out;
}

/// <0 if a<b, 0 if equal, >0 if a>b. Unparseable versions sort lowest.
int compareVersions(String? a, String? b) {
  final pa = _parts(a), pb = _parts(b);
  if (pa == null && pb == null) return 0;
  if (pa == null) return -1;
  if (pb == null) return 1;
  final len = pa.length > pb.length ? pa.length : pb.length;
  for (var i = 0; i < len; i++) {
    final x = i < pa.length ? pa[i] : 0;
    final y = i < pb.length ? pb[i] : 0;
    if (x != y) return x.compareTo(y);
  }
  return 0;
}

bool isNewerVersion(String? candidate, String? current) =>
    _parts(candidate) != null && _parts(current) != null && compareVersions(candidate, current) > 0;

/// Why a plugin needing [minAppVersion] cannot run on this app, or null if it can.
String? appIncompatibility(String? minAppVersion, {String appVersion = kAppVersion}) {
  if (minAppVersion == null || minAppVersion.isEmpty) return null;
  if (_parts(minAppVersion) == null) return null;
  return compareVersions(appVersion, minAppVersion) < 0
      ? 'Requires PAI app $minAppVersion or newer (you have $appVersion). Update the app first.'
      : null;
}
