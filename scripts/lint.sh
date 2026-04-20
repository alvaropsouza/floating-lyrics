#!/bin/bash
# Script auxiliar para rodar ruff no Git Bash/Linux

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ ! -f ".venv/Scripts/activate" ] && [ ! -f ".venv/bin/activate" ]; then
    echo "[!] Ambiente virtual nao encontrado."
    echo "[*] Execute: python -m venv .venv"
    exit 1
fi

# Ativar ambiente virtual
if [ -f ".venv/Scripts/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/Scripts/activate
else
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

case "${1:-check}" in
    --fix)
        echo "[*] Corrigindo problemas automaticamente..."
        python -m ruff check --fix .
        ;;
    format)
        echo "[*] Formatando codigo..."
        python -m ruff format .
        ;;
    check)
        echo "[*] Verificando codigo..."
        python -m ruff check .
        ;;
    *)
        echo "[*] Verificando codigo..."
        python -m ruff check .
        ;;
esac
