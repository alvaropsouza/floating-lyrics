#!/bin/bash

# Script para monitorar logs do servidor em tempo real

echo "========================================"
echo "  Floating Lyrics - Monitor de Logs"
echo "========================================"
echo ""

# Verificar se o arquivo de log existe
if [ ! -f "server.log" ]; then
    echo "[AVISO] Arquivo server.log não encontrado!"
    echo ""
    echo "O servidor pode não estar rodando ainda."
    echo "Inicie o servidor primeiro com: python main_server_headless.py"
    echo ""
    exit 1
fi

echo "Log encontrado! Mostrando últimas 50 linhas e atualizações em tempo real..."
echo ""
echo "========================================"
echo ""

# Mostrar últimas 50 linhas e seguir novas entradas
tail -f -n 50 server.log
