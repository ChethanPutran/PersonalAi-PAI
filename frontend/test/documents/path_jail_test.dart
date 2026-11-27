import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/documents/path_jail.dart';

/// Parity with the Kotlin FileJail rules (FileJailSettingsTest), on real directories and symlinks.
void main() {
  late Directory tmp;
  late String root;

  setUp(() {
    tmp = Directory.systemTemp.createTempSync('jail');
    root = tmp.resolveSymbolicLinksSync();
  });
  tearDown(() => tmp.deleteSync(recursive: true));

  String dir(String rel) {
    Directory('$root/$rel').createSync(recursive: true);
    return '$root/$rel';
  }

  test('several allowed roots; outside is refused', () {
    final j = PathJail(allowed: [dir('sd/Pictures'), dir('sd/Download')]);
    expect(j.allows('$root/sd/Pictures/a.txt'), isTrue);
    expect(j.allows('$root/sd/Download/b.txt'), isTrue);
    expect(j.allows('$root/sd/Other/c.txt'), isFalse);
  });

  test('a denied folder is refused inside an allowed root', () {
    final sd = dir('sd');
    final j = PathJail(allowed: [sd], denied: [dir('sd/private')]);
    expect(j.allows('$sd/ok.txt'), isTrue);
    expect(j.allows('$sd/private/k.txt'), isFalse);
    expect(j.allows('$sd/x/../private/k.txt'), isFalse);       // dot-dot cannot sneak in
  });

  test('a symlink into a denied folder, or out of the boundary, is refused', () {
    final sd = dir('sd');
    final secret = dir('sd/private');
    final outside = dir('elsewhere');
    Link('$sd/shortcut').createSync(secret);
    Link('$sd/escape').createSync(outside);
    final j = PathJail(allowed: [sd], denied: [secret]);
    expect(j.allows('$sd/shortcut/k.txt'), isFalse);
    expect(j.allows('$sd/escape/s.txt'), isFalse);
  });

  test('prefix siblings are not inside a root or a denied folder', () {
    final pics = dir('sd/Pictures');
    dir('sd/Pictures2');
    expect(PathJail(allowed: [pics]).allows('$root/sd/Pictures2/x.txt'), isFalse);
    final sd = dir('sd');
    final j = PathJail(allowed: [sd], denied: [dir('sd/priv')]);
    expect(j.allows('$root/sd/private2/y.txt'), isTrue);
    expect(j.allows('$root/sd/priv/y.txt'), isFalse);
  });

  test('the more specific rule wins between an allowed root and a denied folder', () {
    final data = dir('sd/Android/data');
    final mine = dir('sd/Android/data/app/files');
    final theirs = dir('sd/Android/data/other');
    var j = PathJail(allowed: [mine], denied: [data]);
    expect(j.allows('$mine/m.txt'), isTrue);
    expect(j.allows('$theirs/t.txt'), isFalse);
    final inner = dir('sd/Android/data/app/files/keep');
    j = PathJail(allowed: [mine], denied: [data, inner]);
    expect(j.allows('$inner/z.txt'), isFalse);
    expect(PathJail(allowed: [mine], denied: [mine]).allows('$mine/m.txt'), isFalse);   // denied AND allowed: denied
  });

  test('no allowed folder refuses everything; relative and NUL paths are refused', () {
    final j = PathJail(allowed: const []);
    expect(j.roots, isEmpty);
    expect(j.allows('$root/a.txt'), isFalse);
    final j2 = PathJail(allowed: [dir('sd')]);
    expect(j2.allows('a.txt'), isFalse);
    expect(j2.allows('$root/sd/a\u0000.txt'), isFalse);
  });

  test('a root given through a symlink is canonicalized', () {
    final real = dir('real');
    Link('$root/alias').createSync(real);
    final j = PathJail(allowed: ['$root/alias']);
    expect(j.roots, [real]);
    expect(j.allows('$real/a.txt'), isTrue);
  });

  test('non-existing paths still resolve (no throw)', () {
    final sd = dir('sd');
    expect(PathJail(allowed: [sd]).allows('$sd/new/deep/file.txt'), isTrue);
  });

  test('hasDeniedInside tells when a whole folder would reach into a denied one', () {
    final sd = dir('sd');
    final j = PathJail(allowed: [sd], denied: [dir('sd/private')]);
    expect(j.hasDeniedInside(sd), isTrue);
    expect(j.hasDeniedInside(dir('sd/public')), isFalse);
  });
}
