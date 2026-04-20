@echo off
REM Dev mode para a LLM Music API sem rebuild a cada alteracao no src/
REM Usa bind mounts + node --watch dentro do container.

setlocal
cd /d "%~dp0"

set "REBUILD=0"
if /I "%~1"=="--rebuild" set "REBUILD=1"

echo.
echo ============================================================
echo   LLM Music API - Dev Mode
echo ============================================================
echo.

if not exist .env (
    echo [!] Arquivo .env nao encontrado.
    echo [*] Copiando .env.example para .env...
    copy .env.example .env >nul
    echo [!] Revise o .env e execute novamente, se necessario.
)

if "%REBUILD%"=="1" (
    echo [*] Reconstruindo containers (--rebuild)...
    docker compose -f docker-compose.yml -f docker-compose.dev.yml build llm-music-api model-server
    echo [*] Subindo stack dev do llm-music-api...
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
) else (
    echo [*] Subindo stack dev do llm-music-api (sem rebuild)...
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --no-build
)
if errorlevel 1 (
    echo [!] Falha ao subir a stack do llm-music-api.
    pause
    exit /b 1
)

echo.
echo [OK] Stack dev iniciada.
echo [i] API:   http://localhost:3000/health
echo [i] Model: http://localhost:8000/health
echo [i] Alteracoes em llm-music-api\src\*.js recarregam automaticamente.
echo [i] Para forcar rebuild: start-dev.bat --rebuild
echo [i] Para ver logs:
echo     docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f
echo.

endlocal

