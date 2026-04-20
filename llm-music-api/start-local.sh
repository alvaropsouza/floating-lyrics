#!/bin/bash
# Inicia o llm-music-api localmente (sem Docker) usando pnpm

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

MODEL_SERVER_PID=""

cleanup() {
    if [ -n "${MODEL_SERVER_PID}" ] && kill -0 "${MODEL_SERVER_PID}" 2>/dev/null; then
        echo
        echo "[*] Encerrando model-server..."
        kill "${MODEL_SERVER_PID}" 2>/dev/null || true
        wait "${MODEL_SERVER_PID}" 2>/dev/null || true
    fi
}

trap cleanup EXIT INT TERM

echo
echo "============================================================"
echo "  LLM Music API - Local Mode (No Docker)"
echo "============================================================"
echo

if [ ! -f ".env" ]; then
    echo "[!] Arquivo .env nao encontrado."
    echo "[*] Copiando .env.example para .env..."
    cp .env.example .env
    echo "[!] Revise o .env e configure as variaveis, se necessario."
fi

if [ ! -d "node_modules" ]; then
    echo "[*] Instalando dependencias Node.js com pnpm..."
    pnpm install
fi

echo "[*] Verificando dependencias Python para o model-server..."
if [ ! -d ".venv" ]; then
    echo "[*] Criando ambiente virtual Python..."
    python -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate || source .venv/Scripts/activate

echo "[*] Instalando dependencias Python..."
pip install -q -r requirements.txt

echo "[*] Validando configuracao..."
if ! python validate_config.py; then
    echo "[!] Validacao falhou. Corrija os erros acima."
    exit 1
fi

echo "[*] Iniciando model-server (Python) em background..."
python src/model_server.py &
MODEL_SERVER_PID=$!

sleep 2

echo "[*] Iniciando API Node.js (Fastify)..."
pnpm start
