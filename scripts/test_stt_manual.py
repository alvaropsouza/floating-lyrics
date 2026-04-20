#!/usr/bin/env python3
"""
Teste manual de Speech-to-Text e Lyrics Matcher.

Uso:
    python scripts/test_stt_manual.py [--test-recognizer|--test-matcher|--all]
"""

import logging
import sys
import os
from pathlib import Path

# Configurar encoding para UTF-8 no Windows
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Adicionar raiz do projeto ao path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.speech_recognition import SpeechRecognizer, SpeechSegment
from src.lyrics_matcher import LyricsMatcher, LyricMatch
import numpy as np

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOG = logging.getLogger(__name__)


def test_recognizer():
    """Testa SpeechRecognizer com áudio sintético."""
    print("\n" + "="*70)
    print("🎤 TESTE 1: SpeechRecognizer")
    print("="*70)
    
    try:
        _LOG.info("Inicializando SpeechRecognizer...")
        rec = SpeechRecognizer(model_size="tiny", device="cuda")
        
        info = rec.get_info()
        _LOG.info(f"Modelo carregado: {info}")
        
        # Teste 1: Áudio silencioso (deve retornar None)
        print("\n1️⃣  Testando áudio silencioso...")
        audio_silence = np.zeros(rec.sample_rate * 3, dtype=np.int16)  # 3s de silêncio
        audio_bytes = audio_silence.tobytes()
        result = rec.recognize_chunk(audio_bytes, sample_rate=rec.sample_rate, duration_s=3.0)
        assert result is None, "Esperava None para áudio silencioso"
        print("✅ Corretamente retornou None para silêncio")
        
        # Teste 2: Áudio com ruído aleatório (pode reconhecer como nada ou ruído)
        print("\n2️⃣  Testando áudio com ruído...")
        audio_noise = np.random.randint(-32768, 32767, rec.sample_rate * 2, dtype=np.int16)
        audio_bytes = audio_noise.tobytes()
        result = rec.recognize_chunk(audio_bytes, sample_rate=rec.sample_rate, duration_s=2.0)
        print(f"Resultado: {result}")
        print("✅ Processamento de ruído concluído sem crashes")
        
        # Teste 3: Tentar carregar áudio de arquivo de debug (se existir)
        debug_dir = Path(project_root) / "cache" / "debug_audio"
        if debug_dir.exists():
            wav_files = sorted(debug_dir.glob("*.wav"))
            if wav_files:
                print(f"\n3️⃣  Testando com {len(wav_files)} arquivo(s) de debug...")
                for wav_file in wav_files[:1]:  # Testar apenas primeiro
                    try:
                        import wave
                        with wave.open(str(wav_file), 'rb') as wav:
                            n_frames = wav.getnframes()
                            audio_data = wav.readframes(n_frames)
                            sample_rate = wav.getframerate()
                            
                            _LOG.info(f"Processando {wav_file.name} ({sample_rate} Hz, {n_frames} frames)...")
                            result = rec.recognize_chunk(
                                audio_data,
                                sample_rate=sample_rate,
                                duration_s=n_frames / sample_rate
                            )
                            
                            if result:
                                print(f"✅ Reconhecido: '{result.text}'")
                                print(f"   Confiança: {result.confidence:.3f}")
                                print(f"   Duração: {result.start_ms}-{result.end_ms}ms")
                            else:
                                print(f"⚠️  Sem texto reconhecido")
                    except ImportError:
                        _LOG.warning("Módulo 'wave' não disponível, pulando teste de arquivo")
                    except Exception as e:
                        _LOG.error(f"Erro ao processar {wav_file.name}: {e}")
            else:
                print(f"⚠️  Nenhum arquivo WAV em {debug_dir}")
        else:
            print(f"\n⚠️  Diretório {debug_dir} não existe")
        
        print("\n✅ Testes do SpeechRecognizer PASSARAM")
        return True
        
    except Exception as e:
        _LOG.error(f"❌ Erro no teste do SpeechRecognizer: {e}", exc_info=True)
        return False


def test_matcher():
    """Testa LyricsMatcher com letras conhecidas."""
    print("\n" + "="*70)
    print("🔍 TESTE 2: LyricsMatcher")
    print("="*70)
    
    try:
        _LOG.info("Inicializando LyricsMatcher...")
        matcher = LyricsMatcher(min_similarity=0.6)
        
        # Letra de exemplo (conhecida)
        lyrics = [
            "Quando a luz dos olhos meus",
            "E a luz dos olhos teus",
            "Resolvem se encontrar",
            "Ai que bom que isso é meu Deus",
            "Ai que bom que isso é meu Deus",
            "Quando a felicidade me alcança",
            "Teu coração que bate junto ao meu",
            "Desejo só ficar te olhando assim",
            "Te vendo piscar os olhos para mim",
            "Te vendo sorrir",
        ]
        
        matcher.set_lyrics(lyrics)
        print(f"✅ Carregadas {len(lyrics)} linhas de letra")
        
        # Teste 1: Match exato (sem variações)
        print("\n1️⃣  Testando match exato...")
        result = matcher.find_best_match(
            "quando a luz dos olhos meus",
            context_window=3,
            current_index=0
        )
        assert result is not None, "Deveria encontrar match exato"
        assert result.line_index == 0, f"Deveria ser linha 0, foi {result.line_index}"
        assert result.similarity > 0.9, f"Similaridade deveria ser > 0.9, foi {result.similarity}"
        print(f"✅ Match exato encontrado: linha {result.line_index}, sim={result.similarity:.3f}")
        
        # Teste 2: Match com variações (pontuação, acentuação)
        print("\n2️⃣  Testando match com variações...")
        result = matcher.find_best_match(
            "quando a luz do olho meu",  # Variação: "do olho" em vez de "dos olhos"
            context_window=3,
            current_index=0
        )
        assert result is not None, "Deveria encontrar match com variações"
        assert result.line_index == 0, f"Deveria ser linha 0, foi {result.line_index}"
        print(f"✅ Match com variações: linha {result.line_index}, sim={result.similarity:.3f}")
        
        # Teste 3: Busca com context window
        print("\n3️⃣  Testando busca com context window...")
        result = matcher.find_best_match(
            "desejo só ficar te olhando assim",
            context_window=2,
            current_index=7  # Dica: é a linha 7
        )
        assert result is not None, "Deveria encontrar match com context window"
        assert result.line_index == 7, f"Deveria ser linha 7, foi {result.line_index}"
        print(f"✅ Context window funcionou: linha {result.line_index}")
        
        # Teste 4: Match não encontrado
        print("\n4️⃣  Testando texto não encontrado...")
        result = matcher.find_best_match(
            "texto completamente diferente da musica",
            context_window=5,
            current_index=0
        )
        assert result is None, "Deveria retornar None para texto não encontrado"
        print(f"✅ Corretamente retornou None para texto não encontrado")
        
        # Teste 5: Validação de posição
        print("\n5️⃣  Testando validação de posição...")
        is_valid = matcher.validate_position(
            "quando a luz dos olhos meus",
            expected_index=0,
            tolerance=2
        )
        assert is_valid, "Deveria validar posição correta"
        print(f"✅ Posição validada corretamente")
        
        # Teste 6: Detecção de divergência
        print("\n6️⃣  Testando detecção de divergência...")
        is_valid = matcher.validate_position(
            "desejo só ficar te olhando assim",  # Linha 7
            expected_index=0,  # Mas esperávamos linha 0
            tolerance=2
        )
        assert not is_valid, "Deveria detectar divergência"
        print(f"✅ Divergência detectada corretamente")
        
        print("\n✅ Testes do LyricsMatcher PASSARAM")
        return True
        
    except AssertionError as e:
        _LOG.error(f"❌ Asserção falhou: {e}")
        return False
    except Exception as e:
        _LOG.error(f"❌ Erro no teste do LyricsMatcher: {e}", exc_info=True)
        return False


def main():
    """Executa testes."""
    print("\n" + "="*70)
    print("🧪 TESTE DE INTEGRAÇÃO - Speech-to-Text + Lyrics Matcher")
    print("="*70)
    
    test_matcher_ok = test_matcher()
    test_recognizer_ok = test_recognizer()
    
    # Resumo
    print("\n" + "="*70)
    print("📊 RESUMO DOS TESTES")
    print("="*70)
    print(f"LyricsMatcher:     {'✅ PASSOU' if test_matcher_ok else '❌ FALHOU'}")
    print(f"SpeechRecognizer:  {'✅ PASSOU' if test_recognizer_ok else '❌ FALHOU'}")
    
    if test_matcher_ok and test_recognizer_ok:
        print("\n✅ TODOS OS TESTES PASSARAM!")
        return 0
    else:
        print("\n❌ ALGUNS TESTES FALHARAM")
        return 1


if __name__ == "__main__":
    sys.exit(main())
