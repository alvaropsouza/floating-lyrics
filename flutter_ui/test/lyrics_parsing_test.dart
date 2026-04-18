import 'package:flutter_test/flutter_test.dart';
import 'package:floating_lyrics_ui/models/song.dart';

void main() {
  group('LyricsData parsing', () {
    test('Parse LRC with header (from cache)', () {
      const cacheContent = '''artist=J. Cole
album=2014 Forest Hills Drive
duration_s=292
synced=1
---
[00:18.73] First things first: Rest In Peace Uncle Phil
[00:23.36] You the only father that I ever knew
[00:25.30] I get my bitch pregnant, I'mma be a better you''';

      final lyrics = LyricsData(content: cacheContent, synced: true);

      expect(lyrics.lines.length, 3);
      expect(
          lyrics.lines[0].text, 'First things first: Rest In Peace Uncle Phil');
      expect(lyrics.lines[0].timeMs, 18730);
      expect(lyrics.lines[1].timeMs, 23360);
      expect(lyrics.lines[2].timeMs, 25300);
    });

    test('Parse LRC without header (from WebSocket)', () {
      const wsContent =
          '''[00:18.73] First things first: Rest In Peace Uncle Phil
[00:23.36] You the only father that I ever knew
[00:25.30] I get my bitch pregnant, I'mma be a better you''';

      final lyrics = LyricsData(content: wsContent, synced: true);

      expect(lyrics.lines.length, 3);
      expect(
          lyrics.lines[0].text, 'First things first: Rest In Peace Uncle Phil');
    });

    test('Parse plain text lyrics', () {
      const plainContent = '''Line 1
Line 2
Line 3''';

      final lyrics = LyricsData(content: plainContent, synced: false);

      expect(lyrics.lines.length, 3);
      expect(lyrics.synced, false);
      expect(lyrics.lines[0].timeMs, null);
    });
  });
}
