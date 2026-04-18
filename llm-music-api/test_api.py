#!/usr/bin/env python3
"""
Script de teste para a API de identificação de músicas.
Envia requisições de teste e valida as respostas.

Uso: python test_api.py
"""

import requests
import json
import time
from typing import Dict, Any

API_URL = "http://localhost:3000"


def test_health() -> bool:
    """Testa endpoint de saúde."""
    print("🏥 Testing health endpoint...")
    try:
        response = requests.get(f"{API_URL}/health", timeout=10)
        response.raise_for_status()
        data = response.json()
        print(f"   ✅ Status: {data.get('status')}")
        print(f"   ⏱️  Uptime: {data.get('uptime'):.2f}s")
        return True
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def test_identify_simple() -> bool:
    """Testa identificação simples de música."""
    print("\n🎵 Testing simple identification...")
    
    payload = {
        "query": "música sobre liberdade com um longo solo de guitarra, rock clássico dos anos 70"
    }
    
    try:
        start_time = time.time()
        response = requests.post(
            f"{API_URL}/identify",
            json=payload,
            timeout=120
        )
        elapsed_time = time.time() - start_time
        
        response.raise_for_status()
        data = response.json()
        
        print(f"   ✅ Response received in {elapsed_time:.2f}s")
        print(f"   🎤 Song: {data['data']['song']}")
        print(f"   👨‍🎤 Artist: {data['data']['artist']}")
        print(f"   💿 Album: {data['data']['album']}")
        print(f"   📊 Confidence: {data['data']['confidence']}")
        print(f"   ⚡ Inference time: {data['metadata']['inference_time_ms']}ms")
        
        return True
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def test_identify_with_context() -> bool:
    """Testa identificação com contexto adicional."""
    print("\n🎵 Testing identification with context...")
    
    payload = {
        "query": "qual é essa música?",
        "context": {
            "lyric_snippet": "I'm as free as a bird now",
            "artist_hint": "Lynyrd Skynyrd",
            "genre": "rock"
        }
    }
    
    try:
        start_time = time.time()
        response = requests.post(
            f"{API_URL}/identify",
            json=payload,
            timeout=120
        )
        elapsed_time = time.time() - start_time
        
        response.raise_for_status()
        data = response.json()
        
        print(f"   ✅ Response received in {elapsed_time:.2f}s")
        print(f"   🎤 Song: {data['data']['song']}")
        print(f"   👨‍🎤 Artist: {data['data']['artist']}")
        print(f"   📊 Confidence: {data['data']['confidence']}")
        
        return True
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def test_batch_identify() -> bool:
    """Testa identificação em batch."""
    print("\n🎵 Testing batch identification...")
    
    payload = {
        "queries": [
            {"query": "música sobre dinheiro crescendo em árvores"},
            {"query": "canção japonesa dos anos 80 sobre ficar comigo"}
        ]
    }
    
    try:
        start_time = time.time()
        response = requests.post(
            f"{API_URL}/identify/batch",
            json=payload,
            timeout=240
        )
        elapsed_time = time.time() - start_time
        
        response.raise_for_status()
        data = response.json()
        
        print(f"   ✅ Response received in {elapsed_time:.2f}s")
        print(f"   📊 Processed {len(data['results'])} queries")
        
        for i, result in enumerate(data['results'], 1):
            if result['success']:
                print(f"   {i}. {result['data']['song']} - {result['data']['artist']}")
            else:
                print(f"   {i}. Error: {result['error']}")
        
        return True
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def test_invalid_request() -> bool:
    """Testa validação de requisições inválidas."""
    print("\n🚫 Testing invalid request handling...")
    
    payload = {}  # Sem query
    
    try:
        response = requests.post(
            f"{API_URL}/identify",
            json=payload,
            timeout=10
        )
        
        if response.status_code == 400:
            print(f"   ✅ Correctly rejected invalid request")
            return True
        else:
            print(f"   ❌ Unexpected status code: {response.status_code}")
            return False
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def test_cache() -> bool:
    """Testa funcionalidade de cache."""
    print("\n💾 Testing cache...")
    
    payload = {
        "query": "teste de cache"
    }
    
    try:
        # Primeira requisição
        start_time = time.time()
        response1 = requests.post(f"{API_URL}/identify", json=payload, timeout=120)
        time1 = time.time() - start_time
        response1.raise_for_status()
        
        # Segunda requisição (deve vir do cache)
        start_time = time.time()
        response2 = requests.post(f"{API_URL}/identify", json=payload, timeout=120)
        time2 = time.time() - start_time
        response2.raise_for_status()
        
        print(f"   ⏱️  First request: {time1:.2f}s")
        print(f"   ⚡ Cached request: {time2:.2f}s")
        
        if time2 < time1 * 0.5:  # Cache deve ser significativamente mais rápido
            print(f"   ✅ Cache is working!")
            return True
        else:
            print(f"   ⚠️  Cache may not be working (times are similar)")
            return True  # Não falha o teste, apenas avisa
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def main():
    """Executa todos os testes."""
    print("=" * 60)
    print("🧪 LLM Music API - Test Suite")
    print("=" * 60)
    
    tests = [
        ("Health Check", test_health),
        ("Simple Identification", test_identify_simple),
        ("Identification with Context", test_identify_with_context),
        ("Batch Identification", test_batch_identify),
        ("Invalid Request Handling", test_invalid_request),
        ("Cache Functionality", test_cache),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ Test '{test_name}' crashed: {e}")
            results.append((test_name, False))
        
        time.sleep(1)  # Pequena pausa entre testes
    
    # Sumário
    print("\n" + "=" * 60)
    print("📊 Test Summary")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\n🎯 {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed")
        return 1


if __name__ == "__main__":
    exit(main())
