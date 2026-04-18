@echo off
REM Script para monitorar logs do servidor em tempo real

echo ========================================
echo  Floating Lyrics - Monitor de Logs
echo ========================================
echo.
echo Aguarde...checando se servidor esta rodando...
echo.

REM Verificar se o arquivo de log existe
if not exist "server.log" (
    echo [AVISO] Arquivo server.log nao encontrado!
    echo.
    echo O servidor pode nao estar rodando ainda.
    echo Inicie o servidor com start_server.bat primeiro.
    echo.
    pause
    exit /b 1
)

echo Log encontrado! Mostrando ultimas 50 linhas e atualizacoes em tempo real...
echo.
echo ========================================
echo.

REM Mostrar últimas 50 linhas e seguir novas entradas
powershell -Command "Get-Content server.log -Tail 50 -Wait"
