@echo off
REM Script para build e execução rápida no Windows
REM Uso: start.bat

echo Starting LLM Music API...
echo.

REM Verificar se .env existe
if not exist .env (
    echo WARNING: .env not found. Copying from .env.example...
    copy .env.example .env
    echo Please edit .env with your configurations and run this script again.
    pause
    exit /b 1
)

REM Verificar se pasta models existe
if not exist "models" (
    echo Creating models directory...
    mkdir models\mistral-7b-music-lora
    mkdir models\lora-weights
    echo.
    echo WARNING: Please download your model to .\models\ before starting!
    echo.
    echo Example:
    echo   huggingface-cli download mistralai/Mistral-7B-Instruct-v0.2 ^
    echo     --local-dir .\models\mistral-7b-music-lora ^
    echo     --local-dir-use-symlinks False
    echo.
    pause
)

echo Building Docker image...
docker-compose build

echo.
echo Starting containers...
docker-compose up -d

echo.
echo Waiting for service to be ready...
timeout /t 5 /nobreak >nul

echo.
echo Checking health...
curl -s http://localhost:3000/health >nul 2>&1
if %errorlevel% equ 0 (
    echo Service is healthy!
    echo.
    echo API available at: http://localhost:3000
    echo.
    echo View logs:
    echo   docker-compose logs -f
    echo.
    echo Stop service:
    echo   docker-compose down
) else (
    echo Service failed to start. Check logs:
    echo   docker-compose logs
)

pause
