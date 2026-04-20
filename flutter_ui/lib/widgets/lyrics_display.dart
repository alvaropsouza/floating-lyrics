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
    // SingleChildScrollView + Column garante que todos os itens estão na
    // render tree, permitindo Scrollable.ensureVisible funcionar em qualquer
    // linha — inclusive as que ainda não foram visíveis.
    return Container(
      margin: const EdgeInsets.fromLTRB(10, 8, 10, 10),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        color: Colors.white.withOpacity(0.03),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: SingleChildScrollView(
        controller: _scrollController,
        padding: const EdgeInsets.symmetric(vertical: 30, horizontal: 18),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (int index = 0; index < lyrics.lines.length; index++)
              _buildLyricLine(lyrics, index),
          ],
        ),
      ),
    );
  }

  Widget _buildLyricLine(LyricsData lyrics, int index) {
    final line = lyrics.lines[index];
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
          line.text,
          textAlign: TextAlign.center,
        ),
      ),
    );
  }
}
