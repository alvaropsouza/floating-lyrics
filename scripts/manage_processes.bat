@echo off
REM Script para verificar e gerenciar processos Python do Floating Lyrics

echo.
echo ============================================================
echo   Floating Lyrics - Gerenciador de Processos
echo ============================================================
echo.

:menu
echo Escolha uma opcao:
echo.
echo  1. Verificar processos Python rodando
echo  2. Parar TODOS os processos Python
echo  3. Parar processo especifico (por PID)
echo  4. Verificar porta 8765
echo  5. Sair
echo.
set /p choice="Opcao: "

if "%choice%"=="1" goto check
if "%choice%"=="2" goto killall
if "%choice%"=="3" goto killpid
if "%choice%"=="4" goto checkport
if "%choice%"=="5" goto end
echo Opcao invalida!
goto menu

:check
echo.
echo [*] Processos Python rodando:
echo.
tasklist | findstr /i python.exe
if errorlevel 1 (
    echo Nenhum processo Python encontrado.
) else (
    echo.
    netstat -ano | findstr :8765 | findstr LISTENING
    if errorlevel 1 (
        echo Porta 8765: LIVRE
    ) else (
        echo Porta 8765: EM USO
    )
)
echo.
pause
goto menu

:killall
echo.
echo [!] ATENCAO: Isso vai parar TODOS os processos Python!
set /p confirm="Confirma? (S/N): "
if /i "%confirm%"=="S" (
    echo [*] Parando processos...
    taskkill /F /IM python.exe
    echo.
    echo [OK] Processos parados.
) else (
    echo Cancelado.
)
echo.
pause
goto menu

:killpid
echo.
echo [*] Processos Python disponiveis:
tasklist | findstr /i python.exe
echo.
set /p pid="Digite o PID do processo para parar: "
taskkill /F /PID %pid%
echo.
pause
goto menu

:checkport
echo.
echo [*] Verificando porta 8765...
netstat -ano | findstr :8765
if errorlevel 1 (
    echo [OK] Porta 8765 esta livre
) else (
    echo [!] Porta 8765 em uso
)
echo.
pause
goto menu

:end
echo.
echo Ate logo!
exit /b 0
