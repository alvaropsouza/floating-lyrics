@echo off
REM Inicia o llm-music-api localmente (sem Docker) usando pnpm

setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   LLM Music API - Local Mode (No Docker)
echo ============================================================
echo.

if not exist .env (
    echo [!] Arquivo .env nao encontrado.
    echo [*] Copiando .env.example para .env...
    copy .env.example .env >nul
    echo [!] Revise o .env e configure as variaveis, se necessario.
)

if not exist node_modules (
    echo [*] Instalando dependencias Node.js com pnpm...
    pnpm install
)

echo [*] Verificando dependencias Python para o model-server...
if not exist .venv\Scripts\python.exe (
    echo [*] Criando ambiente virtual Python...
    python -m venv .venv
)

echo [*] Ativando venv e instalando dependencias Python...
call .venv\Scripts\activate.bat
pip install -r requirements.txt >nul 2>&1

echo.
echo [*] Validando configuracao...
python validate_config.py
if errorlevel 1 (
    echo [!] Validacao falhou. Corrija os erros acima.
    pause
    exit /b 1
)

echo.
echo [*] Iniciando model-server (Python) em background...
start "Model Server" cmd /k "cd /d \"%~dp0\" && .venv\Scripts\activate.bat && python src\model_server.py"

timeout /t 2 /nobreak >nul

echo [*] Iniciando API Node.js (Fastify)...
pnpm start

endlocal
