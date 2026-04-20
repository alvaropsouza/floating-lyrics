#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BACKEND_PID=""
START_FRONTEND=true
BACKEND_RELOAD=true

for arg in "$@"; do
    case "$arg" in
        --no-front|--no-frontend)
            START_FRONTEND=false
            ;;
        --no-reload)
            BACKEND_RELOAD=false
            ;;
        *)
            ;;
    esac
done

cleanup() {
    if [ -n "${BACKEND_PID}" ] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
        echo
        echo "[*] Encerrando backend Python..."
        kill "${BACKEND_PID}" 2>/dev/null || true
        wait "${BACKEND_PID}" 2>/dev/null || true
    fi
}

trap cleanup EXIT INT TERM

echo
echo "============================================================"
echo "  Floating Lyrics - Full Dev Stack"
echo "============================================================"
echo

if [ -z "${VIRTUAL_ENV:-}" ] && [ -f ".venv/Scripts/activate" ]; then
    echo "[*] Ativando ambiente virtual..."
    # shellcheck disable=SC1091
    source ".venv/Scripts/activate"
fi

# echo "[*] Iniciando llm-music-api localmente (sem Docker)..."
# "$ROOT_DIR/llm-music-api/start-local.sh" &

if ! python -c "import aiohttp" >/dev/null 2>&1; then
    echo "[*] Instalando dependencias Python headless..."
    pip install -r requirements_headless.txt
fi

if [ "$BACKEND_RELOAD" = true ]; then
    echo "[*] Iniciando backend Python headless com auto-reload em background..."
    python main_server_headless.py --reload &
else
    echo "[*] Iniciando backend Python headless em background..."
    python main_server_headless.py &
fi
BACKEND_PID=$!

echo "[*] Aguardando backend responder..."
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if [ "$START_FRONTEND" = false ]; then
    echo "[*] Modo sem frontend habilitado (--no-front)."
    echo "[OK] Backend headless iniciado na porta 8765."
    echo "[*] Mantendo processo ativo. Pressione Ctrl+C para encerrar."
    wait "${BACKEND_PID}"
else
    echo "[*] Iniciando Flutter..."
    cd "$ROOT_DIR/flutter_ui"
    flutter pub get
    flutter run -d windows
fi

