@echo off
REM Sobe o projeto inteiro em modo desenvolvimento com um unico comando.
REM Abre janelas separadas para: llm-music-api, backend headless e Flutter.

setlocal
cd /d "%~dp0"

set "NO_FRONT=0"
for %%a in (%*) do (
    if /I "%%a"=="--no-front" set "NO_FRONT=1"
    if /I "%%a"=="--no-frontend" set "NO_FRONT=1"
)

echo.
echo ============================================================
echo   Floating Lyrics - Full Dev Stack
echo ============================================================
echo.

REM echo [*] Iniciando llm-music-api localmente (sem Docker)...
REM start "LLM Music API" cmd /k "cd /d "%~dp0llm-music-api" && start-local.bat"

echo [*] Iniciando backend Python headless...
start "Floating Lyrics Backend" cmd /k "cd /d "%~dp0" && start_server.bat"

if "%NO_FRONT%"=="1" (
	echo [*] Modo sem frontend habilitado (--no-front).
) else (
	echo [*] Iniciando Flutter UI...
	start "Floating Lyrics Flutter" cmd /k "cd /d "%~dp0flutter_ui" && run_flutter.bat"
)

echo.
echo [OK] Stack iniciada em janelas separadas.
REM echo [i] A API Node.js roda localmente na porta 3000, model-server na porta 8000.
echo [i] Backend headless rodando na porta 8765 (WebSocket)
echo [i] Flags disponiveis:
echo     --no-front   : Inicia sem Flutter UI
echo.

endlocal
