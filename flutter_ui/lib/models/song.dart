class Song {
  final String title;
  final String artist;
  final String album;

  Song({
    required this.title,
    required this.artist,
    this.album = '',
  });

  factory Song.fromJson(Map<String, dynamic> json) {
    return Song(
      title: json['title'] as String? ?? '',
      artist: json['artist'] as String? ?? '',
      album: json['album'] as String? ?? '',
    );
  }

  @override
  String toString() => '$title - $artist';
}

class LyricsData {
  final String content;
  final bool synced;
  final List<LyricLine> lines;
  final String provider;

  LyricsData({
    required this.content,
    required this.synced,
    List<LyricLine>? lines,
    this.provider = '',
  }) : lines = lines ?? _parseLyrics(content, synced);

  static List<LyricLine> _parseLyrics(String content, bool synced) {
    // Remover cabeçalho do cache (se existir)
    String cleanContent = content;
    if (content.contains('---')) {
      final parts = content.split('---');
      if (parts.length > 1) {
        cleanContent = parts[1].trim();
      }
    }

    if (!synced) {
      // Letras não sincronizadas: cada linha sem timestamp
      return cleanContent
          .split('\n')
          .where((line) => line.trim().isNotEmpty)
          .map((text) => LyricLine(text: text, timeMs: null))
          .toList();
    }

    // Letras sincronizadas (formato LRC)
    final lines = <LyricLine>[];
    final lrcPattern = RegExp(r'\[(\d+):(\d+)\.(\d+)\](.*)');

    for (var line in cleanContent.split('\n')) {
      final match = lrcPattern.firstMatch(line);
      if (match != null) {
        final minutes = int.parse(match.group(1)!);
        final seconds = int.parse(match.group(2)!);
        final centiseconds = int.parse(match.group(3)!);
        final text = match.group(4)!.trim();

        final timeMs =
            (minutes * 60 * 1000) + (seconds * 1000) + (centiseconds * 10);

        if (text.isNotEmpty) {
          lines.add(LyricLine(text: text, timeMs: timeMs));
        }
      }
    }

    return lines;
  }

  /// Retorna true se as letras contêm script não-latino suficiente para
  /// justificar oferecer tradução (japonês, coreano, árabe, hebraico, tailandês…).
  ///
  /// Limiar: >5% dos caracteres imprimíveis em scripts não-latinos.
  bool get needsTranslation {
    if (lines.isEmpty) return false;
    final allText = lines.map((l) => l.text).join('');
    final printable = allText.replaceAll(RegExp(r'\s'), '');
    if (printable.isEmpty) return false;
    final nonLatin = RegExp(
      r'[\u3000-\u9FFF' // CJK / Japonês (hiragana, katakana, kanji)
      r'\uAC00-\uD7AF' // Coreano (hangul)
      r'\u0600-\u06FF' // Árabe
      r'\u0590-\u05FF' // Hebraico
      r'\u0E00-\u0E7F' // Tailandês
      r'\u0400-\u04FF]', // Cirílico (russo, ucraniano…)
    );
    final count = nonLatin.allMatches(printable).length;
    return (count / printable.length) > 0.05;
  }
}

class LyricLine {
  final String text;
  final int? timeMs;

  LyricLine({required this.text, this.timeMs});
}
