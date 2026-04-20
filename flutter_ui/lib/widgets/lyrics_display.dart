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

  @override
  void initState() {
    super.initState();

    // Timer para sincronização (atualiza a cada 100ms)
    _syncTimer = Timer.periodic(const Duration(milliseconds: 100), (_) {
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

    if (lyrics == null || !lyrics.synced) return;

    if (lyrics.lines.isEmpty) {
      debugPrint('ALERTA _updateCurrentLine: lyrics.lines está vazio!');
      return;
    }

    // Usar posição calculada em tempo real ao invés de timecodeMs estático
    final currentPositionMs = ws.currentPositionMs;

    // Debug ocasional
    if (_currentLineIndex == 0 && currentPositionMs > 1000) {
      debugPrint(
          '📍 Posição atual: ${currentPositionMs}ms (${(currentPositionMs / 1000).toStringAsFixed(1)}s)');
    }

    // Encontrar linha atual baseada na posição calculada
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

  void _scrollToCurrentLine() {
    if (!_scrollController.hasClients) return;

    // Scroll suave para a linha atual (centralizada)
    const itemHeight = 60.0; // Altura aproximada de cada linha
    final targetOffset = (_currentLineIndex * itemHeight) -
        (_scrollController.position.viewportDimension / 2) +
        (itemHeight / 2);

    _scrollController.animateTo(
      targetOffset.clamp(0.0, _scrollController.position.maxScrollExtent),
      duration: const Duration(milliseconds: 300),
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

        return _buildLyricsList(lyrics);
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

  Widget _buildLyricsList(LyricsData lyrics) {
    return Container(
      margin: const EdgeInsets.fromLTRB(10, 8, 10, 10),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        color: Colors.white.withOpacity(0.03),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: ListView.builder(
        controller: _scrollController,
        padding: const EdgeInsets.symmetric(vertical: 30, horizontal: 18),
        itemCount: lyrics.lines.length,
        itemBuilder: (context, index) {
          final line = lyrics.lines[index];
          final isActive = lyrics.synced && index == _currentLineIndex;
          final isPast = lyrics.synced && index < _currentLineIndex;

          return AnimatedContainer(
            duration: const Duration(milliseconds: 220),
            margin: const EdgeInsets.symmetric(vertical: 8),
            padding: EdgeInsets.symmetric(
              vertical: isActive ? 10 : 6,
              horizontal: 10,
            ),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(12),
              color: isActive
                  ? Colors.white.withOpacity(0.09)
                  : Colors.transparent,
            ),
            child: AnimatedDefaultTextStyle(
              duration: const Duration(milliseconds: 220),
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
                line.text,
                textAlign: TextAlign.center,
              ),
            ),
          );
        },
      ),
    );
  }
}
