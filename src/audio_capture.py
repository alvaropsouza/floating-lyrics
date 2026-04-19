"""
Captura de áudio via WASAPI loopback (saída do sistema, não microfone).

Dependência: pyaudiowpatch — fork do PyAudio com suporte nativo a WASAPI
loopback no Windows.  Instale com:  pip install pyaudiowpatch
"""

import io
import logging
import threading
import time
import wave
from typing import Optional

_LOG = logging.getLogger(__name__)

try:
    import numpy as _np
    _NUMPY_AVAILABLE = True
except ImportError:
    import math
    import struct
    _NUMPY_AVAILABLE = False

try:
    import pyaudiowpatch as pyaudio  # type: ignore
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _PYAUDIO_AVAILABLE = False


class AudioCaptureError(Exception):
    """Raised for any audio-capture related failure."""


class AudioCapture:
    """
    Captures the Windows system audio output (loopback) using WASAPI.

    Usage::

        cap = AudioCapture(config)
        cap.initialize()          # call once at startup
        wav_bytes = cap.capture(10)   # capture 10 s → WAV bytes
        cap.cleanup()             # call on exit
    """

    def __init__(self, config) -> None:
        self._config = config
        self._pa: Optional[object] = None          # instância usada em capture()
        self._pa_spectrum: Optional[object] = None # instância SEPARADA para espectro
        self._lock = threading.RLock()             # lock exclusivo para capture()
        self._spectrum_lock = threading.Lock()     # lock exclusivo para capture_spectrum()
        self._capture_active = threading.Event()   # evita contencao do loopback durante capture()
        self._last_spectrum: list[float] = [0.0] * 32  # Cache do último espectro
        self._spectrum_cache_hits = 0  # Contador de cache hits consecutivos

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """
        Initialise PyAudio and verify that a WASAPI loopback device exists.

        Raises:
            AudioCaptureError: if pyaudiowpatch is missing or no loopback
                device is found.
        """
        if not _PYAUDIO_AVAILABLE:
            raise AudioCaptureError(
                "pyaudiowpatch não está instalado.\n"
                "Execute no terminal:  pip install pyaudiowpatch"
            )
        self._pa = pyaudio.PyAudio()
        # Instância separada para espectro — sem conflito de lock com capture()
        self._pa_spectrum = pyaudio.PyAudio()
        # Probe the loopback device early so the error surfaces at startup.
        self._get_loopback_device()

    def cleanup(self) -> None:
        """Release PyAudio resources."""
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                _LOG.debug("Erro ao terminar PyAudio", exc_info=True)
            finally:
                self._pa = None
        if self._pa_spectrum is not None:
            try:
                self._pa_spectrum.terminate()
            except Exception:
                _LOG.debug("Erro ao terminar PyAudio (espectro)", exc_info=True)
            finally:
                self._pa_spectrum = None

    # ── Device discovery ────────────────────────────────────────────────────

    def _list_loopback_devices(self) -> list[dict]:
        devices: list[dict] = []
        try:
            for dev in self._pa.get_loopback_device_info_generator():
                devices.append(dev)
        except Exception:
            _LOG.warning("Falha ao listar dispositivos loopback", exc_info=True)
            return []
        return devices

    def _preferred_loopback_device(self) -> Optional[dict]:
        """
        Resolve a user-preferred loopback device by index or partial name.

        Config keys (section [Audio]):
          - capture_device_index (int, default -1)
          - capture_device_name (string, partial match)
        """
        loopbacks = self._list_loopback_devices()
        if not loopbacks:
            return None

        preferred_idx = self._config.getint("Audio", "capture_device_index", fallback=-1)
        if preferred_idx >= 0:
            for dev in loopbacks:
                if int(dev.get("index", -1)) == preferred_idx:
                    return dev
            available = ", ".join(str(int(dev.get("index", -1))) for dev in loopbacks)
            raise AudioCaptureError(
                "capture_device_index inválido no config.ini. "
                f"Índices disponíveis: {available}"
            )

        preferred_name = self._config.get("Audio", "capture_device_name", fallback="").strip().lower()
        if preferred_name:
            for dev in loopbacks:
                name = str(dev.get("name", "")).lower()
                if preferred_name in name:
                    return dev
            names = "; ".join(str(dev.get("name", "")) for dev in loopbacks)
            raise AudioCaptureError(
                "capture_device_name não encontrado no config.ini. "
                f"Dispositivos loopback disponíveis: {names}"
            )

        return None

    def _get_loopback_device(self) -> dict:
        """
        Return the WASAPI loopback device info dict for the current default
        audio output.

        Raises:
            AudioCaptureError: if WASAPI is unavailable or no loopback device
                is found.
        """
        preferred = self._preferred_loopback_device()
        if preferred is not None:
            return preferred

        try:
            wasapi_info = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            raise AudioCaptureError(
                "WASAPI não está disponível neste sistema.\n"
                "Este recurso requer Windows Vista ou superior com drivers WASAPI."
            )

        default_out_idx: int = wasapi_info.get("defaultOutputDevice", -1)
        if default_out_idx < 0:
            raise AudioCaptureError(
                "Nenhum dispositivo de saída de áudio padrão encontrado.\n"
                "Verifique se há alto-falantes ou fones conectados."
            )

        default_out = self._pa.get_device_info_by_index(default_out_idx)

        # Some drivers already expose the loopback variant directly.
        if default_out.get("isLoopbackDevice", False):
            return default_out

        # Search among all loopback devices for the one matching our output.
        try:
            for loopback in self._pa.get_loopback_device_info_generator():
                if default_out["name"] in loopback["name"]:
                    return loopback
        except Exception:
            pass

        raise AudioCaptureError(
            f"Nenhum dispositivo de loopback encontrado para "
            f"'{default_out['name']}'.\n"
            "Certifique-se de que o dispositivo de saída está ativo e "
            "tente reiniciar o aplicativo."
        )

    # ── Silence detection / normalization ───────────────────────────────────

    @staticmethod
    def _rms(raw_bytes: bytes) -> float:
        """Compute Root-Mean-Square of 16-bit little-endian PCM samples."""
        if not raw_bytes:
            return 0.0
        if _NUMPY_AVAILABLE:
            samples = _np.frombuffer(raw_bytes, dtype="<i2").astype(_np.float32)
            return float(_np.sqrt(_np.mean(samples ** 2)))
        # Pure-Python fallback
        import math, struct
        count = len(raw_bytes) // 2
        shorts = struct.unpack_from(f"<{count}h", raw_bytes)
        return math.sqrt(sum(s * s for s in shorts) / count)

    def _normalize(self, raw_bytes: bytes) -> bytes:
        """
        Scale 16-bit PCM with conservative gain to improve recognition while
        avoiding hiss/distortion caused by over-amplifying background noise.
        """
        if not raw_bytes:
            return raw_bytes
        max_gain = self._config.getfloat("Audio", "max_normalize_gain", fallback=3.0)
        target_peak = 22000.0
        if _NUMPY_AVAILABLE:
            samples = _np.frombuffer(raw_bytes, dtype="<i2").astype(_np.float32)
            peak = float(_np.abs(samples).max())
            if peak < 10 or peak >= 28000:
                return raw_bytes
            gain = min(target_peak / peak, max_gain)
            if gain <= 1.0:
                return raw_bytes
            return _np.clip(samples * gain, -32768, 32767).astype("<i2").tobytes()
        # Pure-Python fallback
        import struct
        count = len(raw_bytes) // 2
        fmt = f"<{count}h"
        samples = struct.unpack_from(fmt, raw_bytes)
        peak = max(abs(s) for s in samples)
        if peak < 10 or peak >= 28000:
            return raw_bytes
        gain = min(target_peak / peak, max_gain)
        if gain <= 1.0:
            return raw_bytes
        return bytes(struct.pack(fmt, *(max(-32768, min(32767, int(s * gain))) for s in samples)))

    @staticmethod
    def _peak(raw_bytes: bytes) -> float:
        """Compute absolute peak of 16-bit little-endian PCM samples."""
        if not raw_bytes:
            return 0.0
        if _NUMPY_AVAILABLE:
            samples = _np.frombuffer(raw_bytes, dtype="<i2").astype(_np.float32)
            return float(_np.abs(samples).max()) if samples.size else 0.0
        import struct
        count = len(raw_bytes) // 2
        shorts = struct.unpack_from(f"<{count}h", raw_bytes)
        return float(max(abs(s) for s in shorts)) if shorts else 0.0

    def _downmix_to_mono(self, raw_audio: bytes, channels: int) -> tuple[bytes, int]:
        """Convert PCM 16-bit audio to mono for more stable fingerprinting."""
        if not raw_audio or channels <= 1:
            return raw_audio, 1
        try:
            if _NUMPY_AVAILABLE:
                samples = _np.frombuffer(raw_audio, dtype="<i2")
                if samples.size == 0:
                    return raw_audio, 1
                mono = samples.reshape(-1, channels).astype(_np.float32).mean(axis=1)
                return _np.clip(mono, -32768, 32767).astype("<i2").tobytes(), 1
            import struct
            count = len(raw_audio) // 2
            shorts = struct.unpack_from(f"<{count}h", raw_audio)
            frames = len(shorts) // channels
            mixed = []
            for idx in range(frames):
                frame = shorts[idx * channels:(idx + 1) * channels]
                mixed.append(int(sum(frame) / max(1, len(frame))))
            return struct.pack(f"<{len(mixed)}h", *mixed), 1
        except Exception as exc:
            _LOG.warning("Falha no downmix para mono: %s", exc)
            return raw_audio, channels

    def _resample_if_needed(self, raw_audio: bytes, in_rate: int, channels: int) -> tuple[bytes, int]:
        """Optionally resample to a configured target rate for stable timing."""
        target_rate = self._config.getint("Audio", "target_sample_rate", fallback=44100)
        if target_rate <= 0 or target_rate == in_rate:
            return raw_audio, in_rate
        try:
            if not _NUMPY_AVAILABLE:
                _LOG.warning(
                    "Resample desativado: NumPy não disponível (mantendo %dHz)",
                    in_rate,
                )
                return raw_audio, in_rate

            samples = _np.frombuffer(raw_audio, dtype="<i2")
            if samples.size == 0:
                return raw_audio, in_rate

            if channels > 1:
                samples_2d = samples.reshape(-1, channels)
                in_len = samples_2d.shape[0]
                out_len = max(1, int(round(in_len * (target_rate / in_rate))))
                x_old = _np.arange(in_len, dtype=_np.float64)
                x_new = _np.linspace(0, in_len - 1, out_len, dtype=_np.float64)

                out = _np.empty((out_len, channels), dtype=_np.float32)
                for ch in range(channels):
                    out[:, ch] = _np.interp(x_new, x_old, samples_2d[:, ch])
                converted = _np.clip(out, -32768, 32767).astype("<i2").reshape(-1).tobytes()
            else:
                in_len = samples.shape[0]
                out_len = max(1, int(round(in_len * (target_rate / in_rate))))
                x_old = _np.arange(in_len, dtype=_np.float64)
                x_new = _np.linspace(0, in_len - 1, out_len, dtype=_np.float64)
                out = _np.interp(x_new, x_old, samples.astype(_np.float32))
                converted = _np.clip(out, -32768, 32767).astype("<i2").tobytes()

            _LOG.info("Resample de %dHz para %dHz aplicado", in_rate, target_rate)
            return converted, target_rate
        except Exception as exc:
            _LOG.warning("Falha no resample (%d -> %d): %s", in_rate, target_rate, exc)
            return raw_audio, in_rate

    # ── Capture ─────────────────────────────────────────────────────────────

    def capture_spectrum(self, duration_ms: int = 100, num_bars: int = 32) -> list[float]:
        """
        Captura um snapshot rápido de áudio e retorna espectro de frequências.
        
        Usa instância PyAudio própria (_pa_spectrum) — nunca compete com capture().
        """
        if self._capture_active.is_set():
            return self._last_spectrum.copy()

        # Lock próprio do espectro — totalmente independente do lock de capture()
        acquired = self._spectrum_lock.acquire(blocking=False)
        if not acquired:
            # Outra chamada de espectro ainda rodando — retornar cache sem decay
            return self._last_spectrum.copy()
        
        try:
            spectrum = self._capture_spectrum_unsafe(duration_ms, num_bars)
            self._last_spectrum = spectrum.copy()
            self._spectrum_cache_hits = 0
            return spectrum
        finally:
            self._spectrum_lock.release()
    
    def _capture_spectrum_unsafe(self, duration_ms: int = 100, num_bars: int = 32) -> list[float]:
        """Versão sem lock - usada internamente após adquirir lock de espectro."""
        if self._pa_spectrum is None:
            # Fallback para _pa se espectro não inicializado
            if self._pa is None:
                return [0.0] * num_bars
            pa = self._pa
        else:
            pa = self._pa_spectrum
        
        try:
            device = self._get_loopback_device()
            sample_rate: int = int(device["defaultSampleRate"])
            channels: int = min(int(device["maxInputChannels"]), 2) or 1
            
            chunk = 512  # Chunk menor para captura mais rápida
            
            # Abrir stream de forma mais tolerante a erros
            stream = None
            try:
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=sample_rate,
                    input=True,
                    input_device_index=int(device["index"]),
                    frames_per_buffer=chunk,
                )
                
                # Capturar apenas 2 chunks (~20ms) para ser bem rápido
                frames = []
                for _ in range(2):
                    data = stream.read(chunk, exception_on_overflow=False)
                    frames.append(data)

                raw_audio = b"".join(frames)
                
            except Exception as stream_exc:
                _LOG.debug(f"Failed to capture spectrum audio: {stream_exc}")
                return [0.0] * num_bars
            finally:
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
            
            # Calcular espectro usando FFT
            if len(raw_audio) == 0:
                return [0.0] * num_bars
                
            # Calcular FFT
            if _NUMPY_AVAILABLE:
                samples = _np.frombuffer(raw_audio, dtype="<i2").astype(_np.float32)
                
                # Se stereo, pegar apenas um canal
                if channels > 1:
                    samples = samples[::channels]
                
                # Aplicar FFT
                fft_data = _np.abs(_np.fft.rfft(samples))
                
                # Limitar espectro FFT para frequências musicais (até ~16kHz)
                # Acima disso há pouca/nenhuma energia em música comum
                # Usar apenas os primeiros 66% dos bins FFT (~16kHz de 24kHz)
                spectrum_size = int(len(fft_data) * 0.66)
                fft_data = fft_data[:spectrum_size]
                
                bars = []
                
                for i in range(num_bars):
                    # Mapeamento logarítmico bem suave (expoente 1.3)
                    # Distribui: graves (esquerda), médios (centro), agudos (direita)
                    start_idx = int((i / num_bars) ** 1.3 * spectrum_size)
                    end_idx = int(((i + 1) / num_bars) ** 1.3 * spectrum_size)
                    end_idx = max(start_idx + 1, end_idx)
                    
                    # Média da banda
                    band_avg = float(_np.mean(fft_data[start_idx:end_idx]))
                    
                    # Boost progressivo para frequências altas (elas têm menos energia naturalmente)
                    # Últimas 30% das barras recebem boost crescente
                    if i > num_bars * 0.7:
                        boost = 1.0 + (i - num_bars * 0.7) / (num_bars * 0.3) * 2.0  # até 3x
                        band_avg *= boost
                    
                    bars.append(band_avg)
                
                # Verificar nível geral de áudio ANTES de normalizar
                max_raw = max(bars) if bars else 0.0
                
                # Se o nível geral é muito baixo, retornar zeros (sem áudio/apenas ruído)
                if max_raw < 1000:  # Threshold absoluto para ruído de fundo
                    return [0.0] * num_bars
                
                # Normalizar para [0.0, 1.0]
                if bars and max_raw > 0:
                    bars = [min(1.0, b / max_raw * 1.5) for b in bars]  # 1.5x para dar dinâmica
                    
                    # Noise gate reduzido para permitir agudos mais baixos
                    bars = [b if b > 0.10 else 0.0 for b in bars]
                
                return bars
            else:
                # Fallback sem numpy: retornar valores baseados em RMS
                rms = self._rms(raw_audio)
                base_level = min(1.0, rms / 5000.0)
                return [base_level] * num_bars
                
        except Exception as exc:
            _LOG.debug(f"Erro ao capturar espectro: {exc}")
            return [0.0] * num_bars

    def capture(self, duration: int) -> bytes:
        """
        Record *duration* seconds of system audio via WASAPI loopback.

        Returns:
            WAV-formatted bytes suitable for sending to a recognition API.

        Raises:
            AudioCaptureError: if the capture device is unavailable, no
                audio is detected (silence), or any other recording error.
        """
        # Lock para evitar acesso simultâneo ao WASAPI
        with self._lock:
            self._capture_active.set()
            try:
                return self._capture_unsafe(duration)
            finally:
                self._capture_active.clear()
    
    def _capture_unsafe(self, duration: int) -> bytes:
        """Versão sem lock - usada internamente após adquirir lock."""
        if self._pa is None:
            raise AudioCaptureError(
                "AudioCapture não foi inicializado. Chame initialize() antes."
            )

        device = self._get_loopback_device()
        sample_rate: int = int(device["defaultSampleRate"])
        channels: int = min(int(device["maxInputChannels"]), 2) or 1
        frames: list[bytes] = []

        # Let the WASAPI driver pick the optimal buffer size first (0 = unspecified).
        # Fall back to explicit sizes if the driver rejects paFramesPerBufferUnspecified.
        chunk_candidates = [0, 512, 1024, 2048, 4096]
        stream = None
        last_exc: Exception | None = None
        for chunk in chunk_candidates:
            try:
                stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=sample_rate,
                    input=True,
                    input_device_index=int(device["index"]),
                    frames_per_buffer=chunk,
                )
                break
            except OSError as exc:
                _LOG.debug("Falha ao abrir stream com chunk=%d: %s", chunk, exc)
                last_exc = exc
                # Re-initialize PyAudio for the next attempt — a failed open can
                # leave the WASAPI handle in a broken state.
                try:
                    self._pa.terminate()
                except Exception:
                    pass
                self._pa = pyaudio.PyAudio()

        if stream is None:
            raise AudioCaptureError(
                f"Não foi possível abrir o stream de áudio após {len(chunk_candidates)} tentativas.\n"
                f"Último erro: {last_exc}\n"
                "Tente trocar o dispositivo de saída de áudio ou reiniciar o aplicativo."
            )

        # Use a fixed read chunk of 1024 for the read loop regardless of the
        # buffer size used to open the stream.
        read_chunk = 1024 if chunk == 0 else chunk
        capture_started_at = 0.0
        capture_elapsed_s = 0.0
        try:
            total_chunks = max(1, round((sample_rate / read_chunk) * duration))
            capture_started_at = time.perf_counter()
            for _ in range(total_chunks):
                data = stream.read(read_chunk, exception_on_overflow=False)
                frames.append(data)
            capture_elapsed_s = max(0.001, time.perf_counter() - capture_started_at)
        except OSError as exc:
            raise AudioCaptureError(f"Erro durante a gravação: {exc}") from exc
        finally:
            stream.stop_stream()
            stream.close()

        raw_audio = b"".join(frames)
        captured_channels = channels

        # Estimate the effective sample rate from captured byte count. Some
        # WASAPI drivers may report/open at one rate but deliver data at
        # another, which makes the WAV play accelerated/slow if the header
        # uses only the nominal rate.
        # Keep the device sample rate in the WAV header. Estimating an
        # "effective" rate from wall-clock time is too noisy here and can
        # introduce audible artifacts when we resample afterward.
        effective_rate = sample_rate
        if captured_channels > 0:
            bytes_per_sample = 2  # paInt16
            total_samples = len(raw_audio) / (captured_channels * bytes_per_sample)
            reference_window_s = capture_elapsed_s if capture_elapsed_s > 0 else max(0.001, float(duration))
            estimated_rate = int(round(total_samples / reference_window_s))
            if 8000 <= estimated_rate <= 192000:
                deviation = abs(estimated_rate - sample_rate) / max(sample_rate, 1)
                if deviation >= 0.10:
                    _LOG.warning(
                        "Taxa nominal=%dHz e estimada=%dHz divergiram %.1f%% (janela=%.3fs). "
                        "Mantendo taxa nominal no WAV para evitar reamostragem incorreta.",
                        sample_rate,
                        estimated_rate,
                        deviation * 100,
                        reference_window_s,
                    )

        # ── Silence check (before normalization, on raw signal) ─────────────
        # Use a very low threshold here — just enough to reject true silence.
        # The actual normalization will happen next, so the API always gets
        # a loud signal regardless of system volume.
        raw_audio, channels = self._downmix_to_mono(raw_audio, channels)
        silence_threshold = self._config.getfloat("Recognition", "silence_threshold", fallback=30.0)
        raw_rms = self._rms(raw_audio)
        raw_peak = self._peak(raw_audio)
        _LOG.debug("RMS do áudio bruto: %.1f", raw_rms)
        if raw_rms < silence_threshold and raw_peak < (silence_threshold * 4):
            raise AudioCaptureError(
                f"Nenhum áudio detectado no sistema (RMS = {raw_rms:.1f}).\n"
                "Verifique se algo está tocando."
            )

        # ── Normalize with conservative gain (avoid hiss) ────────────────────
        raw_audio = self._normalize(raw_audio)

        # ── Optional resample to stable target rate ──────────────────────────
        raw_audio, effective_rate = self._resample_if_needed(raw_audio, effective_rate, channels)

        # ── Encode as WAV ───────────────────────────────────────────────────
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(self._pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(effective_rate)
            wf.writeframes(raw_audio)

        return buf.getvalue()
