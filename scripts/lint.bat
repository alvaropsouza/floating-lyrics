@echo off
REM Script auxiliar para rodar ruff no Windows

setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\activate.bat (
    echo [!] Ambiente virtual nao encontrado.
    echo [*] Execute: python -m venv .venv
    exit /b 1
)

call .venv\Scripts\activate.bat

if "%1"=="--fix" (
    echo [*] Corrigindo problemas automaticamente...
    python -m ruff check --fix .
) else if "%1"=="format" (
    echo [*] Formatando codigo...
    python -m ruff format .
) else if "%1"=="check" (
    echo [*] Verificando codigo...
    python -m ruff check .
) else (
    echo [*] Verificando codigo...
    python -m ruff check .
)

endlocal
