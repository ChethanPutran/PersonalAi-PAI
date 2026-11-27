import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/documents/index_diff.dart';

void main() {
  const a = FileMeta(100, 10);

  test('new and changed files are sent, unchanged are not', () {
    final plan = planSync(
      current: {'/a': a, '/b': const FileMeta(200, 5), '/c': const FileMeta(300, 7)},
      server: {'/a': a, '/b': const FileMeta(150, 5)},
    );
    expect(plan.toSend, ['/b', '/c']);
    expect(plan.toRemove, isEmpty);
  });

  test('a size change alone counts as changed; sub-millisecond mtime noise does not', () {
    expect(planSync(current: {'/a': const FileMeta(100, 11)}, server: {'/a': a}).toSend, ['/a']);
    expect(planSync(current: {'/a': const FileMeta(100.0004, 10)}, server: {'/a': a}).toSend, isEmpty);
  });

  test('server paths that are not eligible any more are removed', () {
    final plan = planSync(current: {'/a': a}, server: {'/a': a, '/gone': a, '/denied': a});
    expect(plan.toRemove, ['/denied', '/gone']);
  });

  test('an incomplete walk never removes anything', () {
    final plan = planSync(current: {'/a': a}, server: {'/a': a, '/gone': a}, walkComplete: false);
    expect(plan.toRemove, isEmpty);
  });

  test('files already read and found empty are not retried until they change', () {
    final plan = planSync(current: {'/e': a, '/f': const FileMeta(5, 1)}, server: {}, empties: {'/e': a, '/f': const FileMeta(4, 1)});
    expect(plan.toSend, ['/f']);
  });
}
