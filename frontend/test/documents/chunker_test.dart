import 'package:flutter_test/flutter_test.dart';
import 'package:pai_mobile/documents/chunker.dart';

void main() {
  const c = TextChunker(maxChars: 100, overlapChars: 20);

  test('short text is one chunk', () {
    expect(c.chunk('Hello world.'), ['Hello world.']);
  });

  test('empty / blank text gives no chunks', () {
    expect(c.chunk(''), isEmpty);
    expect(c.chunk('  \n\n \t '), isEmpty);
  });

  test('every chunk fits and nothing is lost', () {
    final text = List.generate(40, (i) => 'Sentence number $i is here.').join(' ');
    final chunks = c.chunk(text);
    expect(chunks.length, greaterThan(5));
    expect(chunks.every((x) => x.length <= 100), isTrue);
    for (var i = 0; i < 40; i++) {
      expect(chunks.any((x) => x.contains('number $i is')), isTrue, reason: 'sentence $i missing');
    }
  });

  test('paragraphs stay together when they fit', () {
    final chunks = const TextChunker(maxChars: 60, overlapChars: 0).chunk('First paragraph here.\n\nSecond one.\n\nThird is longer than the rest of them.');
    expect(chunks.first, contains('First paragraph here.'));
    expect(chunks.join('|'), contains('Third is longer'));
  });

  test('consecutive chunks overlap a little', () {
    final text = List.generate(30, (i) => 'Alpha$i beta gamma delta.').join(' ');
    final chunks = c.chunk(text);
    final tailWord = chunks[0].split(' ').last;
    expect(chunks[1], contains(tailWord));
  });

  test('a run with no sentence end or spaces is hard split within the limit', () {
    final chunks = c.chunk('x' * 450);
    expect(chunks.every((x) => x.length <= 100), isTrue);
    expect(chunks.join().length, greaterThanOrEqualTo(450));
  });

  test('a long word run splits at spaces', () {
    final chunks = c.chunk(List.filled(60, 'word').join(' '));
    expect(chunks.every((x) => x.length <= 100 && !x.startsWith(' ')), isTrue);
  });

  test('maxChunks caps the result', () {
    final text = List.generate(100, (i) => 'Sentence $i goes here.').join(' ');
    expect(c.chunk(text, maxChunks: 3).length, 3);
  });

  test('control characters and NUL are removed, line endings normalized', () {
    final out = TextChunker.clean('a\u0000b\r\nc\u0007d\r\n\r\n\r\n\r\ne');
    expect(out, 'a b\nc d\n\ne');
  });

  test('default chunk size never exceeds the server limit of 2000', () {
    final big = List.generate(2000, (i) => 'Word$i').join(' ');
    expect(const TextChunker().chunk(big).every((x) => x.length <= 2000), isTrue);
  });
}
