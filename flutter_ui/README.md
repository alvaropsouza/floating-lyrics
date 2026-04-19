# Floating Lyrics - Flutter UI

Frontend moderno em Flutter para o Floating Lyrics.

## 📦 Requisitos

- [Flutter SDK](https://flutter.dev/docs/get-started/install) 3.16+
- Windows 10/11
- Visual Studio 2022 (para build Windows)

## 🚀 Setup

### 1. Instalar Flutter

```bash
# Baixe o Flutter SDK
# https://docs.flutter.dev/get-started/install/windows

# Adicione ao PATH
flutter doctor
```

### 2. Habilitar Windows Desktop

```bash
flutter config --enable-windows-desktop
flutter doctor
```

### 3. Instalar dependências

```bash
cd flutter_ui
flutter pub get
```

## 🏃 Rodar

### Um comando a partir da raiz

```bat
start_all.bat
```

No `bash`/Git Bash`, use `./start_all.sh`.

Isso sobe `llm-music-api`, backend headless e Flutter com um comando.

### 1. Iniciar backend Python

Em um terminal:

```bash
# Na raiz do projeto
python main_server_headless.py
# (ou use: start_server.bat)
```

Isso iniciará:
- Reconhecimento de música (WASAPI)
- WebSocket server em `ws://127.0.0.1:8765/ws`

### 2. Rodar Flutter app

Em outro terminal:

```bash
cd flutter_ui
flutter run -d windows
```

## 📁 Estrutura

```
flutter_ui/
├── lib/
│   ├── main.dart                 # Entry point
│   ├── models/
│   │   └── song.dart            # Modelo de música
│   ├── services/
│   │   └── websocket_service.dart  # Cliente WebSocket
│   ├── widgets/
│   │   ├── lyrics_display.dart   # Widget de letras
│   │   └── floating_window.dart  # Janela flutuante
│   └── screens/
│       └── home_screen.dart      # Tela principal
├── windows/                      # Config Windows
└── pubspec.yaml                  # Dependências
```

## 🔧 Packages Usados

- `web_socket_channel` - Comunicação WebSocket
- `provider` - State management
- `bitsdojo_window` - Janela frameless customizada
- `window_manager` - Controle de janela (always on top)

## 🎨 Funcionalidades

- ✅ Janela flutuante frameless (estilo Discord)
- ✅ Letras sincronizadas em tempo real
- ✅ Scroll automático com a música
- ✅ Transições suaves
- ✅ Always on top
- ✅ Draggable
- ✅ Transparência customizável

## 🐛 Debug

### Ver logs do backend:

```bash
tail -f server.log
```

### Ver conexões WebSocket:

```bash
curl http://127.0.0.1:8765/health
```

Deve retornar:
```json
{"status": "ok", "clients": 1}
```
