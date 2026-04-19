# 🚀 Guia Rápido - Floating Lyrics com Flutter

## 📋 Checklist de Setup

### 1. Instalar Flutter

```powershell
# Baixe o Flutter SDK
# https://docs.flutter.dev/get-started/install/windows

# Extraia para C:\flutter (ou outro local)
# Adicione ao PATH: C:\flutter\bin

# Verifique instalação
flutter doctor
```

### 2. Instalar Visual Studio 2022 (para build Windows)

```powershell
# Baixe Visual Studio 2022 Community
# https://visualstudio.microsoft.com/downloads/

# Durante instalação, selecione:
# - "Desktop development with C++"
# - Windows 10/11 SDK
```

### 3. Habilitar Windows Desktop

```powershell
flutter config --enable-windows-desktop
flutter doctor -v
```

## 🏃 Rodar o Projeto

### Terminal 1: Backend Python

```bash
# Instalar dependências Python (se ainda não fez)
pip install -r requirements.txt

# Rodar servidor backend
python main_server_headless.py
# (ou use: start_server.bat)
```

**Saída esperada:**
```
============================================================
Floating Lyrics - Backend Server Mode
============================================================
WebSocket server rodando em ws://127.0.0.1:8765/ws
Iniciando reconhecimento de música...

✅ Servidor rodando!
   WebSocket: ws://127.0.0.1:8765/ws
   Health: http://127.0.0.1:8765/health

Pressione Ctrl+C para parar
```

### Terminal 2: Frontend Flutter

```bash
# Ir para pasta Flutter
cd flutter_ui

# Instalar dependências
flutter pub get

# Rodar em modo debug
flutter run -d windows

# OU compilar release (mais rápido)
flutter build windows --release
```

**Localização do executável:**
```
flutter_ui\build\windows\runner\Release\floating_lyrics_ui.exe
```

## 🎯 Testar Conexão

### 1. Verificar backend está rodando

```bash
# Deve retornar: {"status": "ok", "clients": 0}
curl http://127.0.0.1:8765/health
```

### 2. Rodar Flutter app

- App deve conectar automaticamente
- Status bar deve ficar verde: ● "Conectado ao Floating Lyrics"

### 3. Tocar música

- Backend captura áudio do sistema
- Flutter recebe eventos em tempo real
- Letras aparecem sincronizadas

## 🐛 Troubleshooting

### Flutter não reconhece comando

```bash
# Adicione ao PATH do Windows:
C:\flutter\bin

# Reinicie terminal e teste
flutter --version
```

### Erro "Visual Studio not found"

```bash
# Execute flutter doctor para diagnosticar
flutter doctor -v

# Instale Visual Studio 2022 com C++ workload
```

### WebSocket não conecta

```bash
# Verifique se backend está rodando
curl http://127.0.0.1:8765/health

# Verifique firewalls
# Porta 8765 deve estar liberada
```

### Hot reload quebra conexão

```bash
# Normal em desenvolvimento
# Reconecta automaticamente após 3-5s
```

## 📦 Build para Distribuição

### Release Build

```bash
cd flutter_ui
flutter build windows --release
```

**Arquivos gerados:**
```
flutter_ui\build\windows\runner\Release\
├── floating_lyrics_ui.exe        # Executável principal
├── flutter_windows.dll           # Runtime Flutter
└── data\                         # Assets e recursos
```

### Criar Instalador (Opcional)

Usar **Inno Setup** ou **Advanced Installer**:

```iss
; Script exemplo Inno Setup
[Setup]
AppName=Floating Lyrics
AppVersion=1.0.0
DefaultDirName={pf}\FloatingLyrics
OutputDir=installer
OutputBaseFilename=FloatingLyrics-Setup

[Files]
Source: "flutter_ui\build\windows\runner\Release\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
Source: "main_server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "main_server_headless.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "src\*"; DestDir: "{app}\src"; Flags: ignoreversion recursesubdirs

[Run]
Filename: "{app}\floating_lyrics_ui.exe"; Description: "Executar Floating Lyrics"; Flags: nowait postinstall skipifsilent
```

## 🎨 Customizar UI

### Alterar cores

Edite `lib/main.dart`:

```dart
theme: ThemeData(
  colorScheme: ColorScheme.fromSeed(
    seedColor: Colors.purple, // Sua cor
    brightness: Brightness.dark,
  ),
),
```

### Alterar tamanho da fonte

Edite `lib/widgets/lyrics_display.dart`:

```dart
fontSize: isActive ? 24 : 18, // Tamanhos maiores
```

### Adicionar transparência

Edite `lib/main.dart`:

```dart
WindowOptions(
  backgroundColor: Colors.black.withOpacity(0.8), // 80% opaco
)
```

## 🔧 Desenvolvimento

### Hot Reload (F5 no VS Code)

```bash
# Mudanças em Dart aplicam instantaneamente
# Sem perder estado do app
```

### Ver logs

```bash
# Flutter
flutter run -d windows -v

# Python backend
tail -f server.log
```

### Debug WebSocket

```bash
# Cliente WebSocket de teste
npm install -g wscat
wscat -c ws://127.0.0.1:8765/ws
```

## 📝 Próximos Passos

1. ✅ Backend Python funcionando
2. ✅ Flutter UI conectando via WebSocket
3. 🚧 Adicionar settings panel no Flutter
4. 🚧 Implementar system tray
5. 🚧 Adicionar animações de transição
6. 🚧 Persistir configurações (SharedPreferences)

## 💡 Dicas

- Use `flutter run --release` para melhor performance
- Backend pode rodar como Windows Service (separadamente)
- Flutter pode empacotar backend dentro do executável
- Considere usar isolates para tarefas pesadas
