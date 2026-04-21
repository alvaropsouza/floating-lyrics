import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/song.dart';

class WebSocketService extends ChangeNotifier {
  static const String _wsUrl = 'ws://127.0.0.1:8765/ws';

  WebSocketChannel? _channel;
  StreamSubscription? _subscription;

  // Estado
  String _status = 'Conectando...';
  Song? _currentSong;
  LyricsData? _lyrics;
  int _timecodeMs = 0;
  int _lrcOffsetMs = 0;
  int? _localTimestamp; // Timestamp local (ms) quando recebeu o timecode
  bool _isConnected = false;
  bool _isPaused = false;
  bool _isDebugOnly = false;
  String? _error;
  List<double> _audioSpectrum = List.filled(32, 0.0);
  int _lastSpectrumNotifyTime = 0;

  // Tradução de letras
  List<String>? _translatedLines;
  bool _isTranslating = false;
  bool _showTranslation = false;

  // Getters
  String get status => _status;
  Song? get currentSong => _currentSong;
  LyricsData? get lyrics => _lyrics;
  int get timecodeMs => _timecodeMs;
  int get lrcOffsetMs => _lrcOffsetMs;
  bool get isConnected => _isConnected;
  bool get isPaused => _isPaused;
  bool get isDebugOnly => _isDebugOnly;
  String? get error => _error;
  List<double> get audioSpectrum => _audioSpectrum;

  // Getters de tradução
  List<String>? get translatedLines => _translatedLines;
  bool get isTranslating => _isTranslating;
  bool get showTranslation => _showTranslation;
  bool get canTranslate =>
      _lyrics != null && _lyrics!.needsTranslation && !_isTranslating;

  /// Calcula posição atual da música em tempo real, com offset de letra aplicado
  int get currentPositionMs {
    if (_localTimestamp == null) return _timecodeMs + _lrcOffsetMs;

    // Tempo decorrido desde que recebemos o timecode (em ms)
    final now = DateTime.now().millisecondsSinceEpoch;
    final elapsedMs = now - _localTimestamp!;

    // Posição = timecode inicial + tempo decorrido + offset de letra
    return _timecodeMs + elapsedMs + _lrcOffsetMs;
  }

  void connect() {
    try {
      _channel = WebSocketChannel.connect(Uri.parse(_wsUrl));
      _isConnected = true;
      _error = null;

      _subscription = _channel!.stream.listen(
        _handleMessage,
        onError: _handleError,
        onDone: _handleDisconnect,
      );

      sendMessage('get_runtime_status', {});

      debugPrint('WebSocket conectado: $_wsUrl');
      notifyListeners();
    } catch (e) {
      _handleError(e);
    }
  }

  void disconnect() {
    _subscription?.cancel();
    _channel?.sink.close();
    _isConnected = false;
    notifyListeners();
  }

  void _handleMessage(dynamic message) {
    try {
      final data = jsonDecode(message as String) as Map<String, dynamic>;
      final type = data['type'] as String?;
      final payload = data['data'] as Map<String, dynamic>?;

      // Log apenas eventos importantes (não audio_spectrum)
      if (type != 'audio_spectrum' && type != 'timecode_updated') {
        debugPrint('WS <- $type');
      }

      switch (type) {
        case 'connected':
          _status = payload?['message'] ?? 'Conectado';
          break;

        case 'status_changed':
          _status = payload?['status'] ?? '';
          break;

        case 'runtime_state':
          _isPaused = payload?['is_paused'] as bool? ?? false;
          if (payload != null && payload['is_debug_only'] is bool) {
            _isDebugOnly = payload['is_debug_only'] as bool;
          }
          break;

        case 'command_result':
          final ok = payload?['ok'] as bool? ?? false;
          if (!ok) {
            _error =
                payload?['message'] as String? ?? 'Falha ao executar comando';
            _status = '⚠️ $_error';
          }
          if (payload != null && payload['is_paused'] is bool) {
            _isPaused = payload['is_paused'] as bool;
          }
          if (payload != null && payload['is_debug_only'] is bool) {
            _isDebugOnly = payload['is_debug_only'] as bool;
          }
          break;

        case 'song_found':
          _currentSong = Song.fromJson(payload ?? {});
          _status = 'Tocando: $_currentSong';
          // Resetar timecode e tradução ao trocar de música
          _timecodeMs = 0;
          _localTimestamp = null;
          _translatedLines = null;
          _showTranslation = false;
          break;

        case 'song_not_found':
          _status = 'Nenhuma música tocando';
          _currentSong = null;
          _lyrics = null;
          _translatedLines = null;
          _showTranslation = false;
          // Resetar sincronização
          _timecodeMs = 0;
          _localTimestamp = null;
          break;

        case 'lyrics_ready':
          final content = payload?['lyrics'] as String? ?? '';
          final synced = payload?['synced'] as bool? ?? false;
          final provider = payload?['provider'] as String? ?? '';
          _lyrics =
              LyricsData(content: content, synced: synced, provider: provider);

          // Não precisa mais do capture_start_time do servidor
          // Vamos inicializar timestamp local quando receber timecode_updated

          if (_lyrics!.lines.isEmpty) {
            debugPrint('⚠️ Letras vazias após parse (${content.length} chars)');
          }
          break;

        case 'lyrics_not_found':
          _lyrics = null;
          _status = 'Letra não encontrada';
          break;

        case 'timecode_updated':
          _timecodeMs = payload?['timecode_ms'] as int? ?? 0;
          _localTimestamp = DateTime.now().millisecondsSinceEpoch;
          break;

        case 'error':
          _error = payload?['message'] as String?;
          if (_error != null) {
            _status = '⚠️ $_error';
          }
          debugPrint('❌ Erro: $_error');
          break;

        case 'audio_spectrum':
          final spectrum = payload?['spectrum'] as List<dynamic>?;
          if (spectrum != null) {
            _audioSpectrum =
                spectrum.map((e) => (e as num).toDouble()).toList();
            // Throttle: notificar apenas a cada 50ms (20 FPS)
            final now = DateTime.now().millisecondsSinceEpoch;
            if (now - _lastSpectrumNotifyTime >= 50) {
              _lastSpectrumNotifyTime = now;
              notifyListeners();
            }
          }
          return;

        default:
          if (type != null) {
            debugPrint('⚠️ Tipo desconhecido: $type');
          }
      }

      notifyListeners();
    } catch (e) {
      debugPrint('Erro ao processar mensagem: $e');
    }
  }

  void _handleError(dynamic error) {
    debugPrint('WebSocket erro: $error');
    _isConnected = false;
    _error = error.toString();
    notifyListeners();

    // Tentar reconectar após 5 segundos
    Future.delayed(const Duration(seconds: 5), () {
      if (!_isConnected) {
        debugPrint('Tentando reconectar...');
        connect();
      }
    });
  }

  void _handleDisconnect() {
    debugPrint('WebSocket desconectado');
    _isConnected = false;
    _isPaused = false;
    _isDebugOnly = false;
    _status = 'Desconectado';
    notifyListeners();

    // Tentar reconectar
    Future.delayed(const Duration(seconds: 3), () {
      if (!_isConnected) {
        connect();
      }
    });
  }

  // Enviar mensagens para o backend
  void sendMessage(String type, Map<String, dynamic> data) {
    if (_channel != null && _isConnected) {
      final message = jsonEncode({'type': type, 'data': data});
      _channel!.sink.add(message);
      debugPrint('WS -> $type');
    }
  }

  void ping() {
    sendMessage('ping', {});
  }

  void togglePause() {
    sendMessage('toggle_pause', {});
  }

  void pause() {
    sendMessage('pause', {});
  }

  void resume() {
    sendMessage('resume', {});
  }

  void setDebugOnly(bool enabled) {
    sendMessage('debug_only', {'enabled': enabled});
  }

  /// Ajusta o offset de sincronização das letras por [deltaMs] milissegundos.
  /// Valores positivos avançam as letras (mostram a próxima linha mais cedo).
  /// Valores negativos atrasam as letras.
  void adjustLrcOffset(int deltaMs) {
    _lrcOffsetMs += deltaMs;
    notifyListeners();
  }

  void resetLrcOffset() {
    _lrcOffsetMs = 0;
    notifyListeners();
  }

  void toggleDebugOnly() {
    setDebugOnly(!_isDebugOnly);
  }

  // ── Tradução de letras ────────────────────────────────────────────────────

  /// Alterna entre original e traduzido. Se ainda não traduziu, inicia a tradução.
  Future<void> toggleTranslation() async {
    if (_translatedLines != null) {
      _showTranslation = !_showTranslation;
      notifyListeners();
    } else {
      await translateLyrics();
    }
  }

  /// Traduz todas as linhas das letras via Google Translate (endpoint livre, sem chave).
  Future<void> translateLyrics() async {
    if (_lyrics == null || _lyrics!.lines.isEmpty || _isTranslating) return;

    _isTranslating = true;
    notifyListeners();

    try {
      final originalLines = _lyrics!.lines.map((l) => l.text).toList();
      // Separador improvável de aparecer em letras musicais
      const sep = '\u200B|\u200B';
      final joined = originalLines.join(sep);

      final uri = Uri.parse(
        'https://translate.googleapis.com/translate_a/single'
        '?client=gtx&sl=auto&tl=pt-BR&dt=t'
        '&q=${Uri.encodeComponent(joined)}',
      );

      final response = await http.get(uri).timeout(const Duration(seconds: 15));

      if (response.statusCode == 200) {
        final raw = jsonDecode(response.body) as List<dynamic>;
        // raw[0] = lista de [translated_chunk, original_chunk, ...]
        final chunks = raw[0] as List<dynamic>;
        final fullTranslation =
            chunks.map((c) => (c as List<dynamic>)[0] as String).join('');
        // Reconstituir linhas pelo separador (o Google pode ajustar espaços em torno dele)
        final translatedLines = fullTranslation
            .split(RegExp(r'\u200B\s*\|\s*\u200B'))
            .map((l) => l.trim())
            .toList();
        // Garantir mesmo número de linhas que o original
        if (translatedLines.length == originalLines.length) {
          _translatedLines = translatedLines;
        } else {
          // Fallback: alinhar o que chegou, preencher restante com original
          _translatedLines = List.generate(
            originalLines.length,
            (i) => i < translatedLines.length
                ? translatedLines[i]
                : originalLines[i],
          );
        }
        _showTranslation = true;
        debugPrint('Tradução concluída: ${_translatedLines!.length} linhas');
      } else {
        debugPrint('Erro na tradução: HTTP ${response.statusCode}');
      }
    } catch (e) {
      debugPrint('Erro ao traduzir letras: $e');
    } finally {
      _isTranslating = false;
      notifyListeners();
    }
  }

  @override
  void dispose() {
    disconnect();
    super.dispose();
  }
}
