#!/usr/bin/env python3
"""
Teste de integração STT com worker_headless.py - Fase 2

Valida que:
1. STT está disponível e importa corretamente
2. worker_headless pode inicializar com STT
3. Callbacks STT funcionam
4. Threads STT podem ser criadas/paradas
"""

import sys
import logging
from pathlib import Path
from unittest.mock import Mock, MagicMock

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
_LOG = logging.getLogger(__name__)


def test_imports():
    """Test 1: Importacoes STT"""
    print("\n[TEST 1] Imports")
    print("   " + "-" * 50)
    
    try:
        from src.speech_recognition import SpeechRecognizer
        print("   [OK] SpeechRecognizer imports OK")
        
        from src.lyrics_matcher import LyricsMatcher
        print("   [OK] LyricsMatcher imports OK")
        
        from src.worker_headless import RecognitionWorkerHeadless, _STT_AVAILABLE
        print("   [OK] RecognitionWorkerHeadless imports OK")
        
        if _STT_AVAILABLE:
            print("   [OK] STT available in worker")
        else:
            print("   [WARN] STT not available (missing modules)")
        
        return True
    except ImportError as e:
        print(f"   [FAIL] Import error: {e}")
        return False


def test_worker_initialization():
    """Test 2: Inicializacao do worker com STT"""
    print("\n[TEST 2] Worker init")
    print("   " + "-" * 50)
    
    try:
        from src.worker_headless import RecognitionWorkerHeadless
        from configparser import ConfigParser
        
        # Mock dependencies
        config = ConfigParser()
        config.read_string("""
[Recognition]
confidence_threshold = 0.5
tracking_miss_reset = 3

[SpeechSync]
enabled = false
mode = timestamp_only
model_size = tiny
device = cpu
""")
        
        audio_mock = Mock()
        audio_mock.set_shutdown_flag = Mock()
        audio_mock.capture_chunk = Mock(return_value=None)
        
        recognizer_mock = Mock()
        recognizer_mock.set_fresh_capture_callback = Mock()
        
        lyrics_mock = Mock()
        
        # Criar worker
        worker = RecognitionWorkerHeadless(config, audio_mock, recognizer_mock, lyrics_mock)
        
        print("   [OK] Worker initialized with STT")
        
        # Verificar callbacks
        if 'stt_recognized' in worker._callbacks:
            print("   [OK] Callback 'stt_recognized' registered")
        if 'stt_matched' in worker._callbacks:
            print("   [OK] Callback 'stt_matched' registered")
        if 'sync_corrected' in worker._callbacks:
            print("   [OK] Callback 'sync_corrected' registered")
        
        # Verificar atributos STT
        assert hasattr(worker, '_stt_enabled')
        assert hasattr(worker, '_stt_mode')
        assert hasattr(worker, '_stt_recognizer')
        assert hasattr(worker, '_stt_matcher')
        assert hasattr(worker, '_stt_running')
        print("   [OK] All STT attributes present")
        
        return True
    except Exception as e:
        print(f"   [FAIL] Init error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_stt_methods():
    """Test 3: STT methods work"""
    print("\n[TEST 3] STT methods")
    print("   " + "-" * 50)
    
    try:
        from src.worker_headless import RecognitionWorkerHeadless
        from configparser import ConfigParser
        
        config = ConfigParser()
        config.read_string("""
[Recognition]
confidence_threshold = 0.5
tracking_miss_reset = 3

[SpeechSync]
enabled = false
mode = timestamp_only
""")
        
        audio_mock = Mock()
        audio_mock.set_shutdown_flag = Mock()
        recognizer_mock = Mock()
        recognizer_mock.set_fresh_capture_callback = Mock()
        lyrics_mock = Mock()
        
        worker = RecognitionWorkerHeadless(config, audio_mock, recognizer_mock, lyrics_mock)
        
        # Testar _update_stt_lyrics (deve ser no-op se STT desabilitado)
        worker._update_stt_lyrics("linha um\nlinha dois\nlinha tres")
        print("   [OK] _update_stt_lyrics() works")
        
        # Testar _start_stt_loop (deve ser no-op se STT desabilitado)
        worker._start_stt_loop()
        print("   [OK] _start_stt_loop() works")
        
        # Testar _stop_stt_loop
        worker._stop_stt_loop()
        print("   [OK] _stop_stt_loop() works")
        
        return True
    except Exception as e:
        print(f"   [FAIL] STT methods error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_callbacks():
    """Test 4: STT callbacks work"""
    print("\n[TEST 4] Callbacks")
    print("   " + "-" * 50)
    
    try:
        from src.worker_headless import RecognitionWorkerHeadless
        from configparser import ConfigParser
        
        config = ConfigParser()
        config.read_string("""
[Recognition]
confidence_threshold = 0.5
tracking_miss_reset = 3

[SpeechSync]
enabled = false
""")
        
        audio_mock = Mock()
        audio_mock.set_shutdown_flag = Mock()
        recognizer_mock = Mock()
        recognizer_mock.set_fresh_capture_callback = Mock()
        lyrics_mock = Mock()
        
        worker = RecognitionWorkerHeadless(config, audio_mock, recognizer_mock, lyrics_mock)
        
        # Registrar callbacks
        called = {}
        
        def on_stt_recognized(text, confidence):
            called['recognized'] = (text, confidence)
        
        def on_stt_matched(line_index, similarity):
            called['matched'] = (line_index, similarity)
        
        worker.on('stt_recognized', on_stt_recognized)
        worker.on('stt_matched', on_stt_matched)
        
        # Emitir eventos
        worker._emit('stt_recognized', "teste", 0.9)
        assert 'recognized' in called
        assert called['recognized'] == ("teste", 0.9)
        print("   [OK] Callback 'stt_recognized' works")
        
        worker._emit('stt_matched', 2, 0.85)
        assert 'matched' in called
        assert called['matched'] == (2, 0.85)
        print("   [OK] Callback 'stt_matched' works")
        
        return True
    except Exception as e:
        print(f"   [FAIL] Callbacks error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Executar todos os testes"""
    print("\n[TEST] STT Integration - Fase 2\n")
    
    results = []
    
    results.append(("Imports", test_imports()))
    results.append(("Worker init", test_worker_initialization()))
    results.append(("STT methods", test_stt_methods()))
    results.append(("Callbacks", test_callbacks()))
    
    # Summary
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "[OK]" if result else "[FAIL]"
        print(f"{status} {name}")
    
    print(f"\nScore: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n[OK] FASE 2 - STT Integration ready for next steps")
        return 0
    else:
        print("\n[FAIL] Some tests failed. Check logs above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
