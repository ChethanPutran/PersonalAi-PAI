import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/plugins/permissions/permission_labels.dart';

void main() {
  test('input is described plainly and marked sensitive', () {
    final l = describePermission('input');
    expect(l.title, 'Control your keyboard and mouse');
    expect(l.sensitive, isTrue);
    expect(l.detail, contains('stop it at any time'));
  });

  test('lookup ignores case and surrounding spaces', () {
    expect(describePermission('  Screen ').title, 'See your screen');
  });

  test('an unknown permission is shown as itself, never hidden', () {
    final l = describePermission('teleport');
    expect(l.title, 'teleport');
    expect(l.detail, isEmpty);
    expect(l.sensitive, isFalse);
  });

  test('the permissions that act for the user or see private data are all marked sensitive', () {
    // Mirrors the backend's SENSITIVE_PERMISSIONS (security/permissions/declaration.py).
    for (final p in ['input', 'screen', 'shell', 'process', 'camera', 'microphone', 'clipboard', 'filesystem']) {
      expect(describePermission(p).sensitive, isTrue, reason: p);
    }
    for (final p in ['network', 'notifications']) {
      expect(describePermission(p).sensitive, isFalse, reason: p);
    }
  });
}
