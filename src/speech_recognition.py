"""
Speech-to-Text para sincronização de letras musicais.

Usa faster-whisper para reconhecimento offline de voz cantada.
"""

from __future__ import annotations

import logging
import numpy as np
from dataclasses import dataclass
from typing import Optional

_LOG = logging.getLogger(__name__)


@dataclass
class SpeechSegment:
    """Segmento de texto reconhecido pelo STT."""
    text: str
    confidence: float  # avg_logprob do Whisper: -1.0 a 0.0 (0 = perfeito)
    start_ms: int
    end_ms: int


class SpeechRecognizer:
    """
    Reconhecedor de voz otimizado para música usando Whisper.
    
    Usa faster-whisper com configurações otimizadas para:
    - Baixa latência (modelo tiny/base)
    - Voz cantada (não apenas fala)
    - Português brasileiro
    """
    
    def __init__(self, model_size: str = "tiny", device: str = "cuda"):
        """
        Inicializa reconhecedor de voz.
        
        Args:
            model_size: "tiny", "base", "small", "medium", "large"
                - tiny: ~40MB, 300-500ms, precisão ~75%
                - base: ~75MB, 500-800ms, precisão ~85%
            device: "cuda" (GPU) ou "cpu"
        """
        try:
            from faster_whisper import WhisperModel
            
            _LOG.info(f"🎤 Carregando Whisper model={model_size}, device={device}")
            
            # Detectar device automaticamente se cuda falhar
            actual_device = device
            if device == "cuda":
                try:
                    import torch
                    if not torch.cuda.is_available():
                        _LOG.warning("CUDA não disponível, usando CPU")
                        actual_device = "cpu"
                except ImportError:
                    _LOG.warning("PyTorch não instalado, usando CPU")
                    actual_device = "cpu"
            
            self.model = WhisperModel(
                model_size, 
                device=actual_device,
                compute_type="int8",  # Otimizado para latência
                num_workers=1,        # Threads para processamento
                cpu_threads=4         # Threads CPU
            )
            self.sample_rate = 16000  # Whisper requer 16kHz
            self.device = actual_device
            
            _LOG.info(f"✅ Whisper carregado com sucesso (device={actual_device})")
            
        except Exception as exc:
            _LOG.error(f"❌ Erro ao carregar Whisper: {exc}", exc_info=True)
            raise
    
    def recognize_chunk(
        self, 
        audio_data: bytes, 
        sample_rate: int = 44100,
        duration_s: float = 3.0
    ) -> Optional[SpeechSegment]:
        """
        Reconhece voz em um chunk de áudio.
        
        Args:
            audio_data: Áudio PCM int16 mono (bytes)
            sample_rate: Taxa de amostragem original (padrão 44100 Hz)
            duration_s: Duração esperada do chunk (para logging)
            
        Returns:
            SpeechSegment com texto reconhecido ou None se sem voz
        """
        try:
            # Converter bytes → numpy array float32 [-1.0, 1.0]
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            audio_float = audio_array.astype(np.float32) / 32768.0
            
            # Resample para 16kHz se necessário
            if sample_rate != self.sample_rate:
                audio_float = self._simple_resample(
                    audio_float, 
                    sample_rate, 
                    self.sample_rate
                )
            
            # Verificar se tem energia mínima (não é silêncio)
            energy = np.sqrt(np.mean(audio_float ** 2))
            if energy < 0.001:  # RMS muito baixo = silêncio
                _LOG.debug(f"STT: Áudio muito silencioso (RMS={energy:.6f}), pulando")
                return None
            
            # Transcrever com Whisper
            # Nota: VAD parameters do faster-whisper usam sintaxe diferente
            segments, info = self.model.transcribe(
                audio_float,
                language="pt",           # Português brasileiro
                beam_size=1,             # Beam=1 para latência mínima
                best_of=1,               # Não fazer múltiplas tentativas
                temperature=0.0,         # Greedy decoding (determinístico)
                vad_filter=True,         # Voice Activity Detection (usa default)
                condition_on_previous_text=False  # Não usar contexto anterior (música muda)
            )
            
            # Extrair primeiro segmento com texto
            for segment in segments:
                text = segment.text.strip()
                if text:
                    _LOG.debug(
                        f"🎤 STT reconheceu: '{text}' "
                        f"(conf={segment.avg_logprob:.2f}, "
                        f"{segment.start:.1f}s-{segment.end:.1f}s)"
                    )
                    return SpeechSegment(
                        text=text,
                        confidence=segment.avg_logprob,
                        start_ms=int(segment.start * 1000),
                        end_ms=int(segment.end * 1000)
                    )
            
            _LOG.debug("STT: Nenhum texto reconhecido (provavelmente instrumental)")
            return None
            
        except Exception as exc:
            _LOG.error(f"❌ Erro ao reconhecer áudio: {exc}", exc_info=True)
            return None
    
    @staticmethod
    def _simple_resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
        """
        Resample simples por decimação.
        
        Nota: Para produção, considerar usar scipy.signal.resample_poly
        para qualidade melhor. Esta implementação prioriza velocidade.
        
        Args:
            audio: Array de áudio original
            orig_sr: Sample rate original
            target_sr: Sample rate desejado
            
        Returns:
            Áudio resampleado
        """
        if orig_sr == target_sr:
            return audio
        
        # Decimação simples (pegar 1 a cada N samples)
        # 44100 → 16000: step ≈ 2.75 (pegar ~1 a cada 3)
        step = orig_sr / target_sr
        indices = np.arange(0, len(audio), step).astype(int)
        indices = indices[indices < len(audio)]
        
        return audio[indices]
    
    def get_info(self) -> dict:
        """Retorna informações sobre o modelo carregado."""
        return {
            "sample_rate": self.sample_rate,
            "device": self.device,
            "model": self.model.__class__.__name__,
        }
