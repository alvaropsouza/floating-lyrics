#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

REBUILD=false
for arg in "$@"; do
    case "$arg" in
        --rebuild)
            REBUILD=true
            ;;
        *)
            ;;
    esac
done

echo
echo "============================================================"
echo "  LLM Music API - Dev Mode"
echo "============================================================"
echo

if [ ! -f ".env" ]; then
    echo "[*] Arquivo .env nao encontrado. Copiando de .env.example..."
    cp .env.example .env
fi

if [ "$REBUILD" = true ]; then
    echo "[*] Reconstruindo containers (--rebuild)..."
    docker compose -f docker-compose.yml -f docker-compose.dev.yml build llm-music-api model-server
    echo "[*] Subindo stack dev do llm-music-api..."
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
else
    echo "[*] Subindo stack dev do llm-music-api (sem rebuild)..."
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --no-build
fi

echo
echo "[OK] Stack dev iniciada."
echo "[i] API:   http://localhost:3000/health"
echo "[i] Model: http://localhost:8000/health"
echo "[i] Alteracoes em llm-music-api/src/*.js recarregam automaticamente."
echo "[i] Para forcar rebuild: ./start-dev.sh --rebuild"
echo

