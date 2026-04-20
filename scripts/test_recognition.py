#!/usr/bin/env python3
"""
Script de teste para reconhecimento de áudio usando a LLM Music API.

Uso:
    python test_recognition.py <audio_file.wav>

Exemplo:
    python test_recognition.py llm-music-api/training_audio/mac-miller/swimming/2009.wav
"""

import sys
import base64
import requests
import json
from pathlib import Path


def test_recognition(audio_file: str, api_url: str = "http://127.0.0.1:3000"):
    """Testa reconhecimento de um arquivo de áudio."""
    
    audio_path = Path(audio_file)
    if not audio_path.exists():
        print(f"❌ Arquivo não encontrado: {audio_file}")
        return
    
    print(f"🎵 Testando reconhecimento de: {audio_path.name}")
    print(f"📁 Caminho: {audio_path}")
    print(f"📊 Tamanho: {audio_path.stat().st_size / 1024:.1f} KB")
    print()
    
    # Ler arquivo
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    
    # Codificar em base64
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    
    # Enviar para API
    endpoint = f"{api_url}/identify/audio"
    payload = {
        "audio_base64": audio_b64,
        "top_k": 3
    }
    
    print(f"🔍 Enviando para {endpoint}...")
    print()
    
    try:
        resp = requests.post(
            endpoint,
            json=payload,
            timeout=(3, 30),
            headers={"Content-Type": "application/json"}
        )
        resp.raise_for_status()
        
        result = resp.json()
        
        if result.get("success"):
            matches = result.get("matches", [])
            print(f"✅ Reconhecimento bem-sucedido!")
            print(f"📋 Encontradas {len(matches)} correspondência(s):\n")
            
            for i, match in enumerate(matches, 1):
                print(f"  {i}. 🎵 {match.get('title', '?')}")
                print(f"     👤 {match.get('artist', '?')}")
                print(f"     💿 {match.get('album', '?')}")
                print(f"     🎯 Confiança: {match.get('score', 0):.2%}")
                print()
        else:
            print(f"❌ Falha no reconhecimento")
            print(f"Mensagem: {result.get('message', 'Sem mensagem')}")
            
    except requests.Timeout:
        print("⏱️  Timeout - API demorou muito para responder")
    except requests.ConnectionError:
        print("🔌 Erro de conexão - Verifique se a API está rodando")
        print(f"   URL: {api_url}")
    except requests.HTTPError as e:
        print(f"❌ Erro HTTP {e.response.status_code}")
        try:
            error_data = e.response.json()
            print(f"Mensagem: {error_data.get('message', error_data.get('error', 'Erro desconhecido'))}")
        except:
            print(e.response.text[:200])
    except Exception as e:
        print(f"❌ Erro: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python test_recognition.py <audio_file.wav>")
        print("\nExemplo:")
        print("  python test_recognition.py llm-music-api/training_audio/mac-miller/swimming/2009.wav")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    api_url = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:3000"
    
    test_recognition(audio_file, api_url)
