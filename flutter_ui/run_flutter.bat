@echo off
REM Script para rodar o app Flutter
REM 
REM Pré-requisito: Flutter SDK instalado e no PATH
REM Uso: Clique duas vezes neste arquivo

echo.
echo ============================================================
echo   Floating Lyrics - Flutter Frontend
echo ============================================================
echo.

REM Verificar se Flutter está instalado
where flutter >nul 2>nul
if errorlevel 1 (
    echo [!] Flutter nao encontrado no PATH
    echo.
    echo [i] Instale o Flutter SDK:
    echo     https://docs.flutter.dev/get-started/install/windows
    echo.
    echo [i] Adicione ao PATH:
    echo     C:\flutter\bin
    echo.
    pause
    exit /b 1
)

echo [*] Flutter encontrado: 
flutter --version | findstr "Flutter"
echo.

REM Ir para pasta Flutter
cd flutter_ui

REM Verificar se pubspec.yaml existe
if not exist pubspec.yaml (
    echo [!] Arquivo pubspec.yaml nao encontrado
    echo [!] Execute este script da raiz do projeto
    pause
    exit /b 1
)

REM Instalar dependências
echo [*] Instalando dependencias Flutter...
flutter pub get
if errorlevel 1 (
    echo [!] Erro ao instalar dependencias
    pause
    exit /b 1
)

echo.
echo [*] Rodando app Flutter...
echo.
echo [i] Certifique-se que o backend esta rodando:
echo     python main_server_headless.py
echo     (ou use: start_server.bat)
echo.
echo [i] Hot reload: Pressione R
echo [i] Hot restart: Pressione Shift+R
echo [i] Sair: Pressione Q
echo.

REM Rodar em modo debug
flutter run -d windows

if errorlevel 1 (
    echo.
    echo [!] Erro ao executar Flutter
    pause
    exit /b 1
)
