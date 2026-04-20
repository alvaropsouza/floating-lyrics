@echo off
REM Script para rodar Floating Lyrics em modo servidor (para Flutter)
REM 
REM Uso: Clique duas vezes neste arquivo
REM      OU execute: .\start_server.bat

echo.
echo ============================================================
echo   Floating Lyrics - Backend Server
echo ============================================================
echo.

REM Ativar ambiente virtual se existir
if exist .venv\Scripts\activate.bat (
    echo [*] Ativando ambiente virtual...
    call .venv\Scripts\activate.bat
) else (
    echo [!] Ambiente virtual nao encontrado
    echo [!] Execute: python -m venv .venv
    echo [!] Depois: .venv\Scripts\activate
    echo [!] E entao: pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM Verificar se aiohttp está instalado
python -c "import aiohttp" 2>nul
if errorlevel 1 (
    echo [!] Dependencias nao encontradas
    echo [*] Instalando dependencias (headless - sem PyQt6)...
    pip install -r requirements_headless.txt
    if errorlevel 1 (
        echo [!] Erro ao instalar dependencias
        pause
        exit /b 1
    )
)

REM Verificar se porta 8765 está em uso
echo [*] Verificando disponibilidade da porta...
netstat -ano | findstr :8765 | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
    echo.
    echo [!] ATENCAO: Porta 8765 ja esta em uso!
    echo [!] Provavelmente ha um servidor rodando.
    echo.
    echo [?] Deseja parar processos Python existentes? (S/N)
    set /p "kill_choice="
    if /i "%kill_choice%"=="S" (
        echo [*] Parando processos Python...
        taskkill /F /IM python.exe >nul 2>&1
        timeout /t 2 /nobreak >nul
        echo [OK] Processos parados.
    ) else (
        echo [!] O servidor tentara usar uma porta alternativa.
        timeout /t 2 /nobreak >nul
    )
)

echo [*] Iniciando servidor backend (headless - sem PyQt6)...
echo.
echo [i] O servidor exibira a porta WebSocket em uso
echo [i] Pressione Ctrl+C para parar
echo.

REM Rodar servidor headless
python main_server_headless.py

if errorlevel 1 (
    echo.
    echo [!] Servidor encerrado com erro
    pause
    exit /b 1
)
