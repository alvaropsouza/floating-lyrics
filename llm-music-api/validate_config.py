#!/usr/bin/env python3
"""
Script de validação de configuração para o llm-music-api.
Executa antes de iniciar o servidor para detectar problemas comuns.

Uso:
    python validate_config.py
"""

import os
import sys
from pathlib import Path
from typing import List, Tuple


def validate_model_path() -> Tuple[bool, str]:
    """Valida que MODEL_PATH existe e é acessível."""
    model_path_raw = os.environ.get(
        "MODEL_PATH",
        os.path.join(os.path.dirname(__file__), "models", "mistral-7b-music-lora")
    )
    model_path = Path(model_path_raw).resolve()
    
    if not model_path.exists():
        return False, f"❌ MODEL_PATH não existe: {model_path}"
    
    if not model_path.is_dir():
        return False, f"❌ MODEL_PATH não é um diretório: {model_path}"
    
    # Verificar se contém arquivos de modelo
    has_config = (model_path / "config.json").exists()
    has_safetensors = list(model_path.glob("*.safetensors")) or list(model_path.glob("*.bin"))
    
    if not has_config:
        return False, f"❌ config.json não encontrado em {model_path}"
    
    if not has_safetensors:
        return False, f"❌ Arquivos de pesos (.safetensors ou .bin) não encontrados em {model_path}"
    
    return True, f"✅ MODEL_PATH válido: {model_path}"


def validate_env_file() -> Tuple[bool, str]:
    """Valida que .env existe e tem as variáveis essenciais."""
    env_path = Path(__file__).parent / ".env"
    
    if not env_path.exists():
        return False, f"⚠️  .env não encontrado em {env_path} (usando valores padrão)"
    
    return True, f"✅ .env encontrado"


def validate_node_modules() -> Tuple[bool, str]:
    """Valida que dependências Node.js estão instaladas."""
    node_modules = Path(__file__).parent / "node_modules"
    
    if not node_modules.exists():
        return False, "❌ node_modules não encontrado. Execute: pnpm install"
    
    # Verificar pacotes críticos
    critical_packages = ["fastify", "dotenv"]
    missing = []
    
    for pkg in critical_packages:
        if not (node_modules / pkg).exists():
            missing.append(pkg)
    
    if missing:
        return False, f"❌ Pacotes Node.js faltando: {', '.join(missing)}. Execute: pnpm install"
    
    return True, "✅ Dependências Node.js instaladas"


def validate_python_deps() -> Tuple[bool, str]:
    """Valida que dependências Python estão instaladas."""
    try:
        import torch
        import transformers
    except ImportError as e:
        return False, f"❌ Dependência Python faltando: {e}. Execute: pip install -r requirements.txt"
    
    return True, "✅ Dependências Python instaladas"


def validate_port() -> Tuple[bool, str]:
    """Valida que a porta está disponível."""
    import socket
    
    port = int(os.environ.get("MODEL_SERVER_PORT", "8000"))
    
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True, f"✅ Porta {port} disponível"
        except OSError:
            return False, f"⚠️  Porta {port} já em uso (pode ser normal se o servidor já está rodando)"


def main():
    print("=" * 70)
    print("  LLM Music API - Validação de Configuração")
    print("=" * 70)
    print()
    
    validators = [
        ("Arquivo .env", validate_env_file),
        ("Dependências Node.js", validate_node_modules),
        ("Dependências Python", validate_python_deps),
        ("Caminho do modelo", validate_model_path),
        ("Porta disponível", validate_port),
    ]
    
    results = []
    errors = []
    warnings = []
    
    for name, validator in validators:
        print(f"Validando {name}...", end=" ")
        try:
            success, message = validator()
            results.append((name, success, message))
            
            if success:
                print(message)
            else:
                print(message)
                if "❌" in message:
                    errors.append(message)
                elif "⚠️" in message:
                    warnings.append(message)
        except Exception as e:
            error_msg = f"❌ Erro ao validar {name}: {e}"
            print(error_msg)
            errors.append(error_msg)
    
    print()
    print("=" * 70)
    
    if errors:
        print(f"\n❌ {len(errors)} erro(s) encontrado(s):")
        for err in errors:
            print(f"  {err}")
        print()
        print("Corrija os erros acima antes de iniciar o servidor.")
        sys.exit(1)
    
    if warnings:
        print(f"\n⚠️  {len(warnings)} aviso(s):")
        for warn in warnings:
            print(f"  {warn}")
    
    print("\n✅ Validação concluída com sucesso!")
    print()
    sys.exit(0)


if __name__ == "__main__":
    main()
