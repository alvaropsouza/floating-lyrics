#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BACKEND_PID=""

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

echo "[*] Iniciando llm-music-api em modo dev..."
"$ROOT_DIR/llm-music-api/start-dev.sh"

if ! python -c "import aiohttp" >/dev/null 2>&1; then
    echo "[*] Instalando dependencias Python headless..."
    pip install -r requirements_headless.txt
fi

echo "[*] Iniciando backend Python headless em background..."
python main_server_headless.py &
BACKEND_PID=$!

echo "[*] Aguardando backend responder..."
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "[*] Iniciando Flutter..."
cd "$ROOT_DIR/flutter_ui"
flutter pub get
flutter run -d windows

