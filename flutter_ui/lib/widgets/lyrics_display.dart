import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/websocket_service.dart';
import '../models/song.dart';

class LyricsDisplay extends StatefulWidget {
  const LyricsDisplay({super.key});

  @override
  State<LyricsDisplay> createState() => _LyricsDisplayState();
}

class _LyricsDisplayState extends State<LyricsDisplay> {
  final ScrollController _scrollController = ScrollController();
  Timer? _syncTimer;
  int _currentLineIndex = 0;

  // Uma GlobalKey por linha — permite Scrollable.ensureVisible encontrar a
  // linha exata no render tree sem depender de altura hardcoded.
  List<GlobalKey> _lineKeys = [];

  @override
  void initState() {
    super.initState();

    // 50ms = 20 Hz: responsivo o suficiente sem desperdiçar frame budget.
    _syncTimer = Timer.periodic(const Duration(milliseconds: 50), (_) {
      _updateCurrentLine();
    });
  }

  @override
  void dispose() {
    _syncTimer?.cancel();
    _scrollController.dispose();
    super.dispose();
  }

  void _updateCurrentLine() {
    final ws = context.read<WebSocketService>();
    final lyrics = ws.lyrics;

    if (lyrics == null || !lyrics.synced) {
      if (_lineKeys.isNotEmpty || _currentLineIndex != 0) {
        setState(() {
          _lineKeys = [];
          _currentLineIndex = 0;
        });
      }
      return;
    }

    // Reconstruir chaves quando as letras mudarem (nova música).
    if (_lineKeys.length != lyrics.lines.length) {
      setState(() {
        _lineKeys = List.generate(lyrics.lines.length, (_) => GlobalKey());
        _currentLineIndex = 0;
      });
      return;
    }

    final currentPositionMs = ws.currentPositionMs;

    // Encontrar última linha cujo timestamp já passou.
    int newIndex = 0;
    for (int i = 0; i < lyrics.lines.length; i++) {
      final lineTime = lyrics.lines[i].timeMs;
      if (lineTime != null && lineTime <= currentPositionMs) {
        newIndex = i;
      } else {
        break;
      }
    }

    if (newIndex != _currentLineIndex) {
      setState(() {
        _currentLineIndex = newIndex;
      });
      _scrollToCurrentLine();
    }
  }

  /// Rola para centralizar a linha ativa usando a posição real do widget,
  /// não uma estimativa de altura hardcoded.
  void _scrollToCurrentLine() {
    if (_currentLineIndex >= _lineKeys.length) return;
    final ctx = _lineKeys[_currentLineIndex].currentContext;
    if (ctx == null) return;
    Scrollable.ensureVisible(
      ctx,
      alignment: 0.5, // centraliza no viewport
      duration: const Duration(milliseconds: 200),
      curve: Curves.easeOut,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<WebSocketService>(
      builder: (context, ws, _) {
        final lyrics = ws.lyrics;

        if (lyrics == null) {
          return _buildEmptyState(ws);
        }

        if (lyrics.lines.isEmpty) {
          return const Center(
            child: Text(
              'Letras recebidas mas nenhuma linha foi parseada',
              style: TextStyle(color: Colors.red),
            ),
          );
        }

        // Garantir chaves sincronizadas caso o build rode antes do timer.
        if (_lineKeys.length != lyrics.lines.length) {
          _lineKeys = List.generate(lyrics.lines.length, (_) => GlobalKey());
        }

        return _buildLyricsList(lyrics, ws);
      },
    );
  }

  Widget _buildEmptyState(WebSocketService ws) {
    String message = 'Aguardando música...';
    IconData icon = Icons.music_note_outlined;

    if (!ws.isConnected) {
      message = 'Conectando ao backend...';
      icon = Icons.sync;
    } else if (ws.error != null) {
      message = 'Erro: ${ws.error}';
      icon = Icons.error_outline;
    } else if (ws.currentSong != null) {
      message = 'Letra não encontrada';
      icon = Icons.music_off;
    }

    return Center(
      child: Container(
        margin: const EdgeInsets.symmetric(horizontal: 20),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
        decoration: BoxDecoration(
          color: Colors.white.withOpacity(0.04),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: Colors.white.withOpacity(0.12)),
        ),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 42, color: Colors.white38),
            const SizedBox(height: 10),
            Text(
              message,
              style: const TextStyle(
                color: Colors.white70,
                fontSize: 14,
                fontWeight: FontWeight.w500,
              ),
              textAlign: TextAlign.center,
              overflow: TextOverflow.ellipsis,
              maxLines: 3,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildLyricsList(LyricsData lyrics, WebSocketService ws) {
    // SingleChildScrollView + Column garante que todos os itens estão na
    // render tree, permitindo Scrollable.ensureVisible funcionar em qualquer
    // linha — inclusive as que ainda não foram visíveis.
    final showTranslation = ws.showTranslation && ws.translatedLines != null;
    return Container(
      margin: const EdgeInsets.fromLTRB(10, 8, 10, 10),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        color: Colors.white.withOpacity(0.03),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: Column(
        children: [
          // Header com botão de tradução (exibido só quando aplicável)
          if (ws.canTranslate || ws.translatedLines != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 10, 10, 0),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  if (ws.isTranslating)
                    const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 8),
                      child: SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(
                          strokeWidth: 1.5,
                          color: Colors.white38,
                        ),
                      ),
                    )
                  else
                    Tooltip(
                      message: showTranslation
                          ? 'Mostrar original'
                          : 'Traduzir para português',
                      child: InkWell(
                        onTap: ws.isTranslating ? null : ws.toggleTranslation,
                        borderRadius: BorderRadius.circular(8),
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 8, vertical: 4),
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(8),
                            color: showTranslation
                                ? Colors.teal.withOpacity(0.18)
                                : Colors.white.withOpacity(0.06),
                            border: Border.all(
                              color: showTranslation
                                  ? Colors.teal.withOpacity(0.5)
                                  : Colors.white.withOpacity(0.12),
                            ),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                Icons.translate_rounded,
                                size: 13,
                                color: showTranslation
                                    ? Colors.teal.shade200
                                    : Colors.white38,
                              ),
                              const SizedBox(width: 4),
                              Text(
                                showTranslation ? 'Original' : 'Traduzir',
                                style: TextStyle(
                                  fontSize: 10,
                                  color: showTranslation
                                      ? Colors.teal.shade200
                                      : Colors.white38,
                                  fontWeight: showTranslation
                                      ? FontWeight.w600
                                      : FontWeight.normal,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
          // Lista de letras
          Expanded(
            child: SingleChildScrollView(
              controller: _scrollController,
              padding: const EdgeInsets.symmetric(vertical: 30, horizontal: 18),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (int index = 0; index < lyrics.lines.length; index++)
                    _buildLyricLine(
                      lyrics,
                      index,
                      showTranslation &&
                              index < (ws.translatedLines?.length ?? 0)
                          ? ws.translatedLines![index]
                          : null,
                    ),
                  if (lyrics.provider.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 20, bottom: 4),
                      child: Text(
                        _providerLabel(lyrics.provider),
                        style: const TextStyle(
                          color: Colors.white24,
                          fontSize: 10,
                          fontStyle: FontStyle.italic,
                          letterSpacing: 0.4,
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  static String _providerLabel(String provider) {
    switch (provider) {
      case 'lrclib':
        return 'lrclib.net';
      case 'musixmatch':
        return 'Musixmatch';
      case 'audcr':
        return 'AudD';
      default:
        return provider;
    }
  }

  Widget _buildLyricLine(LyricsData lyrics, int index, String? translatedText) {
    final line = lyrics.lines[index];
    final displayText = translatedText ?? line.text;
    final isActive = lyrics.synced && index == _currentLineIndex;
    final isPast = lyrics.synced && index < _currentLineIndex;
    // Chave para Scrollable.ensureVisible — segura mesmo se ainda não inicializada.
    final key = index < _lineKeys.length ? _lineKeys[index] : null;

    return AnimatedContainer(
      key: key,
      duration: const Duration(milliseconds: 80),
      margin: const EdgeInsets.symmetric(vertical: 8),
      padding: EdgeInsets.symmetric(
        vertical: isActive ? 10 : 6,
        horizontal: 10,
      ),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(12),
        color: isActive ? Colors.white.withOpacity(0.09) : Colors.transparent,
      ),
      child: AnimatedDefaultTextStyle(
        duration: const Duration(milliseconds: 80),
        style: TextStyle(
          fontSize: isActive ? 21 : 16,
          fontWeight: isActive ? FontWeight.w700 : FontWeight.w500,
          color: isActive
              ? Colors.white
              : isPast
                  ? Colors.white38
                  : Colors.white70,
          height: 1.45,
        ),
        child: Text(
          displayText,
          textAlign: TextAlign.center,
        ),
      ),
    );
  }
}
