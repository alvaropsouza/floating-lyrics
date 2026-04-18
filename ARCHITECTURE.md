# Arquitetura: Flutter + Python Backend

## 📐 Visão Geral

```
┌─────────────────────────────────────────────────────────────────┐
│                         FLOATING LYRICS                         │
└─────────────────────────────────────────────────────────────────┘

                    ARQUITETURA HIBRIDA

   ┌───────────────────────┐         ┌───────────────────────┐
   │   FRONTEND (Flutter)  │         │  BACKEND (Python)     │
   │                       │         │                       │
   │  ┌─────────────────┐  │         │  ┌─────────────────┐  │
   │  │  UI Components  │  │         │  │ WASAPI Capture  │  │
   │  │  - Lyrics View  │  │         │  │ (pyaudiowpatch) │  │
   │  │  - Sync Display │  │         │  └────────┬────────┘  │
   │  │  - Window Mgmt  │  │         │           │           │
   │  └────────┬────────┘  │         │  ┌────────▼────────┐  │
   │           │           │         │  │  Recognition    │  │
   │  ┌────────▼────────┐  │         │  │  - AudD         │  │
   │  │  WebSocket      │◄─┼─────────┼─►│  - ACRCloud     │  │
   │  │  Client         │  │   WS    │  └────────┬────────┘  │
   │  └─────────────────┘  │         │           │           │
   │                       │         │  ┌────────▼────────┐  │
   │  Port: Windows        │         │  │ Lyrics Fetcher  │  │
   │  Size: ~60MB          │         │  │  - lrclib.net   │  │
   └───────────────────────┘         │  │  - Musixmatch   │  │
                                     │  └────────┬────────┘  │
                                     │           │           │
                                     │  ┌────────▼────────┐  │
                                     │  │  WebSocket      │  │
                                     │  │  Server         │  │
                                     │  │  (aiohttp)      │  │
                                     │  └─────────────────┘  │
                                     │                       │
                                     │  Port: 8765           │
                                     │  Size: ~30MB          │
                                     └───────────────────────┘
```

## 🔄 Fluxo de Dados

### 1. Inicialização

```
Backend Python (main_server.py):
  1. Carrega Config (config.ini)
  2. Inicializa AudioCapture (WASAPI)
  3. Inicializa MultiProviderRecognizer (AudD + ACRCloud)
  4. Inicializa LyricsFetcher (lrclib + Musixmatch)
  5. Cria RecognitionWorker (QThread)
  6. Inicia WebSocket Server (asyncio, porta 8765)
  7. Conecta sinais Qt → WebSocket Bridge
  8. Worker começa a capturar áudio

Frontend Flutter:
  1. Cria janela frameless (bitsdojo_window)
  2. Inicializa WebSocketService
  3. Conecta em ws://127.0.0.1:8765/ws
  4. Aguarda eventos
```

### 2. Reconhecimento de Música

```
[Backend] Worker Thread (Qt):
  ┌──────────────────────────────────┐
  │ 1. Captura 10s de áudio WASAPI   │
  │    └─> AudioCapture.capture()    │
  └──────────┬───────────────────────┘
             │
  ┌──────────▼───────────────────────┐
  │ 2. Envia para API reconhecimento │
  │    └─> MultiProvider.recognize() │
  │        ├─> Tenta AudD             │
  │        └─> Fallback ACRCloud      │
  └──────────┬───────────────────────┘
             │
  ┌──────────▼───────────────────────┐
  │ 3. Emite signal: song_found      │
  │    └─> worker.song_found.emit()  │
  └──────────┬───────────────────────┘
             │
[Backend] WebSocket Bridge:
  ┌──────────▼───────────────────────┐
  │ 4. Recebe signal (Qt slot)       │
  │    └─> bridge.on_song_found()    │
  └──────────┬───────────────────────┘
             │
  ┌──────────▼───────────────────────┐
  │ 5. Envia para asyncio loop       │
  │    └─> run_coroutine_threadsafe()│
  └──────────┬───────────────────────┘
             │
[Backend] WebSocket Server:
  ┌──────────▼───────────────────────┐
  │ 6. Broadcast para clientes       │
  │    └─> server.emit_song_found()  │
  │        {type: "song_found",      │
  │         data: {title, artist}}   │
  └──────────┬───────────────────────┘
             │ ws://
             │
[Frontend] Flutter:
  ┌──────────▼───────────────────────┐
  │ 7. WebSocket recebe mensagem     │
  │    └─> ws_service._handleMessage()│
  └──────────┬───────────────────────┘
             │
  ┌──────────▼───────────────────────┐
  │ 8. Atualiza estado (Provider)    │
  │    └─> _currentSong = Song(...)  │
  │    └─> notifyListeners()         │
  └──────────┬───────────────────────┘
             │
  ┌──────────▼───────────────────────┐
  │ 9. UI rebuild automático         │
  │    └─> Consumer<WebSocketService>│
  │    └─> Mostra título da música   │
  └──────────────────────────────────┘
```

### 3. Busca e Exibição de Letras

```
[Backend] Worker Thread:
  Música reconhecida
    └─> LyricsFetcher.fetch()
        ├─> Busca em lrclib.net (paralelo ±1s duração)
        ├─> Fallback Musixmatch
        └─> Cache em disco (.txt)
    └─> Emite signal: lyrics_ready(content, synced=True)

[Backend] Bridge → WebSocket:
  └─> {type: "lyrics_ready", data: {lyrics: "...", synced: true}}

[Frontend] Flutter:
  1. Recebe evento lyrics_ready
  2. Parse LRC format → List<LyricLine>
     - Regex: \[(\d+):(\d+)\.(\d+)\](.*)
     - Converte para timeMs
  3. Provider notifica UI
  4. LyricsDisplay rebuild
     - ListView com todas as linhas
     - Timer 100ms sincroniza com timecode
```

### 4. Sincronização em Tempo Real

```
[Backend] Worker emite a cada mudança:
  └─> timecode_updated(timecode_ms=45000, capture_start=...)
  └─> WebSocket → {type: "timecode_updated", data: {timecode_ms: 45000}}

[Frontend] Flutter:
  Timer.periodic(100ms):
    1. Lê ws.timecodeMs
    2. Encontra linha atual onde timeMs <= timecodeMs
    3. Atualiza _currentLineIndex
    4. Scroll automático para linha atual
    5. Destaque visual (tamanho, cor, peso)
```

## 📡 Protocolo WebSocket

### Mensagens Backend → Frontend

```json
// Conexão estabelecida
{
  "type": "connected",
  "data": {"message": "Conectado ao Floating Lyrics"}
}

// Status genérico
{
  "type": "status_changed",
  "data": {"status": "Capturando 10s de áudio..."}
}

// Música encontrada
{
  "type": "song_found",
  "data": {
    "title": "Bohemian Rhapsody",
    "artist": "Queen",
    "album": "A Night at the Opera"
  }
}

// Música não encontrada
{
  "type": "song_not_found",
  "data": {}
}

// Letras prontas
{
  "type": "lyrics_ready",
  "data": {
    "lyrics": "[00:00.00]Is this the real life?\n[00:05.50]Is this just fantasy?",
    "synced": true
  }
}

// Letras não encontradas
{
  "type": "lyrics_not_found",
  "data": {}
}

// Atualização de timecode (sync)
{
  "type": "timecode_updated",
  "data": {"timecode_ms": 45000}
}

// Erro
{
  "type": "error",
  "data": {"message": "Timeout ao conectar ao AudD"}
}
```

### Mensagens Frontend → Backend

```json
// Ping (keepalive)
{
  "type": "ping",
  "data": {}
}

// Resposta esperada
{
  "type": "pong",
  "data": {}
}

// Requisitar status
{
  "type": "get_status",
  "data": {}
}

// Configuração (futuro)
{
  "type": "set_config",
  "data": {
    "capture_duration": 10,
    "recognition_interval": 5
  }
}
```

## 🧵 Threading Model

### Backend Python

```
Main Thread (Qt QCoreApplication):
  └─> Event loop Qt (signals/slots)

Worker Thread (RecognitionWorker extends QThread):
  └─> Loop infinito:
      ├─> Captura áudio (bloqueante 10s)
      ├─> HTTP request reconhecimento (bloqueante 2-15s)
      ├─> HTTP request letras (bloqueante 5-15s)
      └─> Emite signals (thread-safe → Main Thread)

WebSocket Thread (asyncio):
  └─> Event loop asyncio separado
  └─> aiohttp server
  └─> Broadcast para clientes conectados
  └─> run_coroutine_threadsafe() para comunicar com Qt
```

**Comunicação Inter-Thread:**
- Qt → asyncio: `asyncio.run_coroutine_threadsafe(coro, loop)`
- Signals Qt são automaticamente thread-safe (queued connections)

### Frontend Flutter

```
Main Thread (UI):
  └─> Event loop Flutter
  └─> Widgets rebuild em setState()/notifyListeners()

Isolates (se necessário):
  └─> Background processing pesado (não usado ainda)

WebSocket Stream:
  └─> Roda no main thread
  └─> Callbacks síncronos chamam notifyListeners()
```

## 📦 Estrutura de Arquivos

```
floating-lyrics/
├── main.py                    # PyQt6 standalone
├── main_server.py             # Backend server (para Flutter)
├── config.ini                 # Configurações
├── requirements.txt           # Deps Python
│
├── src/
│   ├── audio_capture.py       # WASAPI loopback
│   ├── song_recognition.py    # MultiProvider (AudD + ACRCloud)
│   ├── lyrics_fetcher.py      # lrclib + Musixmatch
│   ├── worker.py              # RecognitionWorker (QThread)
│   ├── websocket_server.py    # aiohttp WebSocket server
│   └── websocket_bridge.py    # Qt signals → WebSocket
│
└── flutter_ui/
    ├── pubspec.yaml           # Deps Flutter
    ├── lib/
    │   ├── main.dart          # Entry point
    │   ├── models/
    │   │   └── song.dart      # Song, LyricsData
    │   ├── services/
    │   │   └── websocket_service.dart  # Cliente WS
    │   ├── screens/
    │   │   └── home_screen.dart        # Tela principal
    │   └── widgets/
    │       └── lyrics_display.dart     # Exibição letras
    └── windows/               # Build config Windows
```

## 🚀 Performance

### Latência Típica

```
Evento                          Tempo
─────────────────────────────────────
Captura áudio (10s)             10s
Reconhecimento API              2-5s
Busca letras                    3-8s
WebSocket broadcast             <10ms
Flutter UI update               16ms (1 frame @ 60fps)
Total (música nova)             15-23s
Total (música em cache)         12-15s
```

### Otimizações Aplicadas

1. **Timeout agressivo**: 10s (era 30s)
2. **Keep-alive HTTP**: Reusa conexões TCP
3. **Tracking mode**: 90% menos reconhecimentos durante playback
4. **Cache em disco**: Evita buscar mesma letra 2×
5. **Workers paralelos limitados**: Max 3 requests simultâneos

## 🔮 Extensões Futuras

### Backend
- [ ] Suporte a outros sistemas (via PulseAudio/JACK)
- [ ] API REST além de WebSocket (GET /status, POST /config)
- [ ] Múltiplos clientes Flutter simultâneos
- [ ] Broadcast via UDP para descoberta automática

### Frontend
- [ ] Settings panel (config.ini via WebSocket)
- [ ] System tray com menu
- [ ] Múltiplos temas/skins
- [ ] Exportar letras (PDF, TXT)
- [ ] Histórico de músicas reconhecidas
- [ ] Animações de transição (fade, slide)

### Integração
- [ ] Empacotar Python dentro do executável Flutter
- [ ] Auto-start backend ao abrir Flutter
- [ ] Instalador único (Inno Setup)
- [ ] Update automático (Sparkle/Squirrel)
