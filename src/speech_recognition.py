"""
Speech-to-Text para sincronização de letras musicais.

Usa faster-whisper para reconhecimento offline de voz cantada.
"""

from __future__ import annotations

import io
import logging
import re
import wave
import numpy as np
from dataclasses import dataclass
from typing import Optional

_LOG = logging.getLogger(__name__)

# Confiança mínima aceitável (avg_logprob do Whisper: 0 = perfeito, -1 = ruim)
_MIN_CONFIDENCE = -0.8

# Probabilidade máxima de "não há voz" — chunks mais instrumentais que vocais são descartados
_MAX_NO_SPEECH_PROB = 0.5


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
    - Detecção automática de idioma (músicas são multilíngues)
    """

    def __init__(self, model_size: str = "tiny", device: str = "cuda"):
        try:
            from faster_whisper import WhisperModel

            _LOG.info("🎤 Carregando Whisper model=%s, device=%s", model_size, device)

            actual_device = device
            if device == "cuda":
                try:
                    import torch
                    if not torch.cuda.is_available():
                        _LOG.warning("CUDA não disponível — usando CPU para Whisper.")
                        actual_device = "cpu"
                except (ImportError, OSError):
                    # OSError = DLL do torch não carregou (ex: c10.dll no Windows)
                    _LOG.warning("PyTorch não pôde ser carregado — usando CPU para Whisper.")
                    actual_device = "cpu"

            try:
                self.model = WhisperModel(
                    model_size,
                    device=actual_device,
                    compute_type="int8",
                    num_workers=1,
                    cpu_threads=4,
                )
            except (RuntimeError, OSError) as dll_exc:
                if actual_device != "cpu":
                    _LOG.warning(
                        "Whisper falhou com device='%s' (%s) — tentando CPU.",
                        actual_device,
                        dll_exc,
                    )
                    actual_device = "cpu"
                    self.model = WhisperModel(
                        model_size,
                        device="cpu",
                        compute_type="int8",
                        num_workers=1,
                        cpu_threads=4,
                    )
                else:
                    raise
            self.sample_rate = 16000  # Whisper requer 16 kHz
            self.device = actual_device

            _LOG.info("✅ Whisper carregado com sucesso (device=%s, model=%s)", actual_device, model_size)

        except Exception as exc:
            _LOG.error("❌ Erro ao carregar Whisper: %s", exc, exc_info=True)
            raise

    # ── Entrada pública ──────────────────────────────────────────────────────

    def recognize_chunk(
        self,
        audio_data: bytes,
        sample_rate: int = 44100,
        duration_s: float = 3.0,
    ) -> Optional[SpeechSegment]:
        """
        Reconhece voz em um chunk de áudio.

        Args:
            audio_data: WAV bytes (com header) OU PCM int16 mono raw.
                        Detectado automaticamente pelo magic bytes.
            sample_rate: Taxa de amostragem original — usado apenas se audio_data
                         for PCM raw (sem header WAV).
            duration_s:  Duração esperada do chunk (para logging).

        Returns:
            SpeechSegment com texto reconhecido ou None se sem voz / baixa confiança.
        """
        try:
            audio_float, actual_sr = self._load_audio(audio_data, sample_rate)

            # Downmix estéreo → mono
            if audio_float.ndim == 2:
                audio_float = audio_float.mean(axis=1)

            # Resample para 16 kHz
            if actual_sr != self.sample_rate:
                audio_float = self._resample(audio_float, actual_sr, self.sample_rate)

            # Verificar energia mínima
            energy = float(np.sqrt(np.mean(audio_float ** 2)))
            if energy < 0.001:
                _LOG.debug("STT: áudio silencioso (RMS=%.6f), pulando.", energy)
                return None

            # Deixar Whisper detectar o idioma automaticamente — músicas são multilíngues
            # temperature como lista: Whisper tenta 0.0 primeiro; se repetição, tenta 0.2
            segments_iter, info = self.model.transcribe(
                audio_float,
                language=None,              # auto-detect de idioma
                beam_size=5,
                best_of=1,
                temperature=[0.0, 0.2],    # fallback para evitar loops de alucinação
                vad_filter=True,
                no_speech_threshold=_MAX_NO_SPEECH_PROB,
                condition_on_previous_text=False,
                compression_ratio_threshold=2.4,  # descarta se muito repetitivo
            )

            # Verificar no_speech_prob do chunk inteiro antes de iterar segmentos
            if hasattr(info, 'no_speech_prob') and info.no_speech_prob > _MAX_NO_SPEECH_PROB:
                _LOG.debug(
                    "STT: chunk descartado — no_speech_prob=%.2f > %.2f (predominantemente instrumental).",
                    info.no_speech_prob, _MAX_NO_SPEECH_PROB,
                )
                return None

            for segment in segments_iter:
                text = segment.text.strip()
                if not text:
                    continue

                # Detectar alucinação de loop (ex: "o que é o que é o que é...")
                if self._is_hallucination(text):
                    _LOG.warning(
                        "STT: alucinação detectada e descartada (padrão repetitivo): '%s...'",
                        text[:60],
                    )
                    continue

                conf = float(segment.avg_logprob)
                if conf < _MIN_CONFIDENCE:
                    _LOG.debug(
                        "STT: segmento '%s' descartado — confiança muito baixa (%.2f < %.2f).",
                        text[:60], conf, _MIN_CONFIDENCE,
                    )
                    continue

                detected_lang = getattr(info, 'language', '?')
                _LOG.info(
                    "🎤 STT reconheceu [%s]: '%s' (confiança=%.2f, %.1fs-%.1fs)",
                    detected_lang, text, conf, segment.start, segment.end,
                )
                return SpeechSegment(
                    text=text,
                    confidence=conf,
                    start_ms=int(segment.start * 1000),
                    end_ms=int(segment.end * 1000),
                )

            _LOG.debug("STT: nenhum texto válido reconhecido no chunk de %.1fs.", duration_s)
            return None

        except Exception as exc:
            _LOG.error("❌ Erro ao reconhecer áudio: %s", exc, exc_info=True)
            return None

    # ── Helpers internos ────────────────────────────────────────────────────

    @staticmethod
    def _is_hallucination(text: str) -> bool:
        """
        Detecta alucinações de loop do Whisper.

        Estratégias:
        1. N-grama repetido: se um bigrama ou trigrama aparece mais de 4× é loop.
        2. Comprimento desproporcional: texto > 300 chars em chunk de música = suspeito.
        """
        if len(text) > 300:
            # Textos muito longos em chunks de música são quase sempre alucinação
            words = text.lower().split()
            if len(words) >= 6:
                # Verificar se bigramas se repetem excessivamente
                bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
                if bigrams:
                    most_common_count = max(bigrams.count(b) for b in set(bigrams))
                    if most_common_count >= 4:
                        return True

        # Verificar padrão de repetição direta com regex (ex: "abc abc abc abc")
        # Detecta qualquer sequência de 2-6 palavras repetida 3+ vezes consecutivas
        pattern = r'\b(.{4,30})\s+\1\s+\1'
        if re.search(pattern, text, re.IGNORECASE):
            return True

        return False

    @staticmethod
    def _load_audio(audio_data: bytes, fallback_sr: int) -> tuple[np.ndarray, int]:
        """
        Carrega áudio a partir de WAV bytes (com header) ou PCM int16 raw.

        Retorna (array float32 [-1, 1], sample_rate).
        Para WAV estéreo, retorna shape (n_frames, n_channels).
        """
        if audio_data[:4] == b"RIFF":
            with wave.open(io.BytesIO(audio_data)) as wf:
                sr = wf.getframerate()
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                raw = wf.readframes(wf.getnframes())

            if sampwidth == 2:
                pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            elif sampwidth == 4:
                pcm = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
            else:
                pcm = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0

            if channels > 1:
                pcm = pcm.reshape(-1, channels)
            return pcm, sr
        else:
            # PCM int16 raw sem header (legado)
            pcm = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            return pcm, fallback_sr

    @staticmethod
    def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
        """
        Resample via scipy (alta qualidade) com fallback para interpolação linear.
        """
        if orig_sr == target_sr:
            return audio

        try:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(orig_sr, target_sr)
            return resample_poly(audio, target_sr // g, orig_sr // g).astype(np.float32)
        except ImportError:
            pass

        # Fallback: interpolação linear
        target_len = int(len(audio) * target_sr / orig_sr)
        x_orig = np.linspace(0, 1, len(audio), endpoint=False)
        x_new = np.linspace(0, 1, target_len, endpoint=False)
        return np.interp(x_new, x_orig, audio).astype(np.float32)

    def get_info(self) -> dict:
        """Retorna informações sobre o modelo carregado."""
        return {
            "sample_rate": self.sample_rate,
            "device": self.device,
            "model": self.model.__class__.__name__,
        }

