@echo off
REM Sobe o projeto inteiro em modo desenvolvimento com um unico comando.
REM Abre janelas separadas para: llm-music-api, backend headless e Flutter.

setlocal
cd /d "%~dp0"

set "NO_FRONT=0"
if /I "%~1"=="--no-front" set "NO_FRONT=1"
if /I "%~1"=="--no-frontend" set "NO_FRONT=1"

echo.
echo ============================================================
echo   Floating Lyrics - Full Dev Stack
echo ============================================================
echo.

echo [*] Iniciando llm-music-api em modo dev...
start "LLM Music API" cmd /k "cd /d \"%~dp0llm-music-api\" && call start-dev.bat"

echo [*] Iniciando backend Python headless...
start "Floating Lyrics Backend" cmd /k "cd /d \"%~dp0\" && call start_server.bat"

if "%NO_FRONT%"=="1" (
	echo [*] Modo sem frontend habilitado (--no-front).
) else (
	echo [*] Iniciando Flutter UI...
	start "Floating Lyrics Flutter" cmd /k "cd /d \"%~dp0\" && call flutter_ui\run_flutter.bat"
)

echo.
echo [OK] Stack iniciada em janelas separadas.
echo [i] Se for o primeiro boot do llm-music-api, o Docker ainda pode levar alguns minutos.
echo.

endlocal
