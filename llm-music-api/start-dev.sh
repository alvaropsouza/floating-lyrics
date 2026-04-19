#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo
echo "============================================================"
echo "  LLM Music API - Dev Mode"
echo "============================================================"
echo

if [ ! -f ".env" ]; then
    echo "[*] Arquivo .env nao encontrado. Copiando de .env.example..."
    cp .env.example .env
fi

echo "[*] Subindo stack dev do llm-music-api..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

echo
echo "[OK] Stack dev iniciada."
echo "[i] API:   http://localhost:3000/health"
echo "[i] Model: http://localhost:8000/health"
echo "[i] Alteracoes em llm-music-api/src/*.js recarregam automaticamente."
echo

