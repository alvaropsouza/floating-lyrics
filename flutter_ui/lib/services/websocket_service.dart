import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/song.dart';

class WebSocketService extends ChangeNotifier {
  static const String _wsUrl = 'ws://127.0.0.1:8765/ws';
  static const Duration _reconnectDelay = Duration(seconds: 3);

  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  Timer? _reconnectTimer;
  bool _isConnecting = false;
  bool _isDisposed = false;
  bool _manualDisconnect = false;

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

  Future<void> connect() async {
    if (_isDisposed || _isConnecting || _isConnected) {
      return;
    }

    _manualDisconnect = false;
    _reconnectTimer?.cancel();
    await _closeConnection(notify: false);

    _isConnecting = true;
    _status = 'Conectando...';
    _error = null;
    _notifySafely();

    final channel = WebSocketChannel.connect(Uri.parse(_wsUrl));

    // Aguardar handshake antes de subscrever o stream — evita que onError
    // dispare em paralelo com o catch e cause duplo _handleError.
    try {
      await channel.ready;
    } catch (e) {
      try {
        await channel.sink.close();
      } catch (_) {}
      _isConnecting = false;
      _handleError(e);
      return;
    }

    if (_isDisposed || _manualDisconnect) {
      try {
        await channel.sink.close();
      } catch (_) {}
      _isConnecting = false;
      return;
    }

    _channel = channel;
    _isConnected = true;
    _isConnecting = false;
    _error = null;

    // Subscrever somente após conexão estabelecida — onError/onDone são apenas
    // para erros e fechamentos em runtime, não para falhas de handshake.
    _subscription = _channel!.stream.listen(
      _handleMessage,
      onError: _handleError,
      onDone: _handleDisconnect,
    );

    debugPrint('WebSocket conectado: $_wsUrl');
    _notifySafely();
    sendMessage('get_runtime_status', {});
  }

  void disconnect() {
    _manualDisconnect = true;
    _reconnectTimer?.cancel();
    unawaited(_closeConnection());
  }

  Future<void> _closeConnection({bool notify = true}) async {
    final subscription = _subscription;
    final channel = _channel;

    _subscription = null;
    _channel = null;
    _isConnected = false;
    _isConnecting = false;

    await subscription?.cancel();
    try {
      await channel?.sink.close();
    } catch (_) {}

    if (notify) {
      _notifySafely();
    }
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
          _handleConnected(payload);
          break;
        case 'status_changed':
          _handleStatusChanged(payload);
          break;
        case 'runtime_state':
          _handleRuntimeState(payload);
          break;
        case 'command_result':
          _handleCommandResult(payload);
          break;
        case 'song_found':
          _handleSongFound(payload);
          break;
        case 'song_not_found':
          _handleSongNotFound();
          break;
        case 'lyrics_ready':
          _handleLyricsReady(payload);
          break;
        case 'lyrics_not_found':
          _handleLyricsNotFound();
          break;
        case 'timecode_updated':
          _handleTimecodeUpdated(payload);
          break;
        case 'error':
          _handleBackendError(payload);
          break;
        case 'audio_spectrum':
          _handleAudioSpectrum(payload);
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

  void _handleConnected(Map<String, dynamic>? payload) {
    _status = payload?['message'] ?? 'Conectado';
  }

  void _handleStatusChanged(Map<String, dynamic>? payload) {
    _status = payload?['status'] ?? '';
  }

  void _handleRuntimeState(Map<String, dynamic>? payload) {
    _isPaused = payload?['is_paused'] as bool? ?? false;
    if (payload != null && payload['is_debug_only'] is bool) {
      _isDebugOnly = payload['is_debug_only'] as bool;
    }
  }

  void _handleCommandResult(Map<String, dynamic>? payload) {
    final ok = payload?['ok'] as bool? ?? false;
    if (!ok) {
      _error = payload?['message'] as String? ?? 'Falha ao executar comando';
      _status = '⚠️ $_error';
    }
    _applyRuntimeFlags(payload);
  }

  void _handleSongFound(Map<String, dynamic>? payload) {
    _currentSong = Song.fromJson(payload ?? {});
    _status = 'Tocando: $_currentSong';
    _resetPlaybackState();
    _resetTranslationState();
  }

  void _handleSongNotFound() {
    _status = 'Nenhuma música tocando';
    _currentSong = null;
    _lyrics = null;
    _resetPlaybackState();
    _resetTranslationState();
  }

  void _handleLyricsReady(Map<String, dynamic>? payload) {
    final content = payload?['lyrics'] as String? ?? '';
    final synced = payload?['synced'] as bool? ?? false;
    final provider = payload?['provider'] as String? ?? '';
    _lyrics = LyricsData(content: content, synced: synced, provider: provider);

    if (_lyrics!.lines.isEmpty) {
      debugPrint('⚠️ Letras vazias após parse (${content.length} chars)');
    }
  }

  void _handleLyricsNotFound() {
    _lyrics = null;
    _status = 'Letra não encontrada';
  }

  void _handleTimecodeUpdated(Map<String, dynamic>? payload) {
    _timecodeMs = payload?['timecode_ms'] as int? ?? 0;
    _localTimestamp = DateTime.now().millisecondsSinceEpoch;
  }

  void _handleBackendError(Map<String, dynamic>? payload) {
    _error = payload?['message'] as String?;
    if (_error != null) {
      _status = '⚠️ $_error';
    }
    debugPrint('❌ Erro: $_error');
  }

  void _handleAudioSpectrum(Map<String, dynamic>? payload) {
    final spectrum = payload?['spectrum'] as List<dynamic>?;
    if (spectrum == null) return;

    _audioSpectrum = spectrum.map((e) => (e as num).toDouble()).toList();
    final now = DateTime.now().millisecondsSinceEpoch;
    if (now - _lastSpectrumNotifyTime >= 50) {
      _lastSpectrumNotifyTime = now;
      notifyListeners();
    }
  }

  void _applyRuntimeFlags(Map<String, dynamic>? payload) {
    if (payload != null && payload['is_paused'] is bool) {
      _isPaused = payload['is_paused'] as bool;
    }
    if (payload != null && payload['is_debug_only'] is bool) {
      _isDebugOnly = payload['is_debug_only'] as bool;
    }
  }

  void _resetPlaybackState() {
    _timecodeMs = 0;
    _localTimestamp = null;
  }

  void _resetTranslationState() {
    _translatedLines = null;
    _showTranslation = false;
  }

  void _handleError(dynamic error) {
    debugPrint('WebSocket erro: $error');
    unawaited(_closeConnection(notify: false));
    _isConnected = false;
    _isConnecting = false;
    _error = error.toString();
    if (!_manualDisconnect) {
      _status = 'Tentando reconectar...';
    }
    _notifySafely();

    _scheduleReconnect();
  }

  void _handleDisconnect() {
    debugPrint('WebSocket desconectado');
    unawaited(_closeConnection(notify: false));
    _isConnected = false;
    _isConnecting = false;
    _isPaused = false;
    _isDebugOnly = false;
    _status = 'Desconectado';
    _notifySafely();
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_isDisposed || _manualDisconnect || _isConnected || _isConnecting) {
      return;
    }

    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(_reconnectDelay, () {
      if (_isDisposed || _manualDisconnect || _isConnected || _isConnecting) {
        return;
      }
      debugPrint('Tentando reconectar...');
      unawaited(connect());
    });
  }

  void _notifySafely() {
    if (!_isDisposed) {
      notifyListeners();
    }
  }

  // Enviar mensagens para o backend
  void sendMessage(String type, Map<String, dynamic> data) {
    if (_channel != null && _isConnected) {
      final message = jsonEncode({'type': type, 'data': data});
      try {
        _channel!.sink.add(message);
        debugPrint('WS -> $type');
      } catch (e) {
        _handleError(e);
      }
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
  /// Cada linha é traduzida individualmente em lotes paralelos para evitar
  /// problemas de separador — o chunk único com separador costuma ser corrompido
  /// pelo API para textos em CJK/árabe/etc.
  Future<void> translateLyrics() async {
    if (_lyrics == null || _lyrics!.lines.isEmpty || _isTranslating) return;

    _isTranslating = true;
    notifyListeners();

    try {
      final originalLines = _lyrics!.lines.map((l) => l.text).toList();
      final translated = <String>[];

      // Processar em lotes de 8 linhas em paralelo para velocidade sem
      // sobrecarregar o endpoint gratuito.
      const batchSize = 8;
      for (int i = 0; i < originalLines.length; i += batchSize) {
        final end = (i + batchSize).clamp(0, originalLines.length);
        final batch = originalLines.sublist(i, end);
        final results = await Future.wait(batch.map(_translateLine));
        translated.addAll(results);
      }

      _translatedLines = translated;
      _showTranslation = true;
      debugPrint('Tradução concluída: ${_translatedLines!.length} linhas');
    } catch (e) {
      debugPrint('Erro ao traduzir letras: $e');
    } finally {
      _isTranslating = false;
      notifyListeners();
    }
  }

  /// Traduz uma única linha. Retorna o original em caso de falha.
  Future<String> _translateLine(String text) async {
    if (text.trim().isEmpty) return text;
    try {
      final uri = Uri.parse(
        'https://translate.googleapis.com/translate_a/single'
        '?client=gtx&sl=auto&tl=pt-BR&dt=t'
        '&q=${Uri.encodeComponent(text)}',
      );
      final response = await http.get(uri).timeout(const Duration(seconds: 10));
      if (response.statusCode == 200) {
        final raw = jsonDecode(response.body) as List<dynamic>;
        final chunks = raw[0] as List<dynamic>;
        return chunks.map((c) => (c as List<dynamic>)[0] as String).join('');
      }
    } catch (_) {}
    return text; // fallback ao texto original
  }

  @override
  void dispose() {
    _isDisposed = true;
    _reconnectTimer?.cancel();
    disconnect();
    super.dispose();
  }
}
