import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
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
          // Resetar timecode ao trocar de música
          _timecodeMs = 0;
          _localTimestamp = null;
          break;

        case 'song_not_found':
          _status = 'Nenhuma música tocando';
          _currentSong = null;
          _lyrics = null;
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

  @override
  void dispose() {
    disconnect();
    super.dispose();
  }
}
