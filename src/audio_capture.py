"""
Captura de áudio via PyAudioWPatch + WASAPI loopback (saída do sistema, não microfone).

Dependência: pyaudiowpatch
  pip install pyaudiowpatch

Funciona exclusivamente no Windows via WASAPI loopback.
"""

import contextlib
import io
import logging
import threading
import time
import wave
from typing import Optional

_LOG = logging.getLogger(__name__)

try:
    import pyaudiowpatch as pyaudio
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _PYAUDIO_AVAILABLE = False

try:
    import numpy as _np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False


class AudioCaptureError(Exception):
    """Raised for any audio-capture related failure."""


class AudioCapture:
    """
    Captures Windows system audio output (loopback) using PyAudioWPatch + WASAPI.

    Usage::

        cap = AudioCapture(config)
        cap.initialize()              # call once at startup
        wav_bytes = cap.capture(10)   # capture 10 s -> WAV bytes
        cap.cleanup()                 # call on exit
    """

    # Parâmetros fixos de captura
    _CAPTURE_CHANNELS = 2
    _FRAMES_PER_BUFFER = 1024

    # Parâmetros do espectro
    _SPECTRUM_BUFFER_MS = 300

    def __init__(self, config) -> None:
        self._config = config
        self._pa: Optional[object] = None
        self._pa_spectrum: Optional[object] = None
        self._device_info: Optional[dict] = None
        self._sample_format = None

        self._lock = threading.RLock()
        self._capture_active = threading.Event()
        self._shutdown_flag: Optional[threading.Event] = None

        self._spectrum_lock = threading.Lock()
        self._spectrum_stream = None
        self._spectrum_buf: bytes = b""
        self._spectrum_buf_lock = threading.Lock()
        self._spectrum_reader: Optional[threading.Thread] = None
        self._spectrum_running = False
        self._last_spectrum: list[float] = [0.0] * 32
        self._spectrum_sample_rate: int = 44100
        self._spectrum_channels: int = 2

        # Timing de capture — atualizados após cada _capture_unsafe()
        # Usados pelo worker para compensação precisa de timecode.
        self._last_audio_end_time: float = 0.0   # perf_counter() após último stream.read()
        self._last_input_latency_s: float = 0.0  # latência reportada pelo WASAPI para o dispositivo

    # ── Lifecycle ───────────────────────────────────────────────────────────

    @property
    def last_audio_end_time(self) -> float:
        """perf_counter() registrado imediatamente após o último stream.read() da captura mais recente."""
        return self._last_audio_end_time

    @property
    def last_input_latency_s(self) -> float:
        """Latência de buffer do dispositivo WASAPI (segundos) reportada pelo stream de captura."""
        return self._last_input_latency_s

    def set_shutdown_flag(self, flag: threading.Event) -> None:
        self._shutdown_flag = flag

    def initialize(self) -> None:
        """Inicializa PyAudio e verifica dispositivo de loopback WASAPI."""
        if not _PYAUDIO_AVAILABLE:
            raise AudioCaptureError(
                "pyaudiowpatch não está instalado.\n"
                "Execute no terminal: pip install pyaudiowpatch"
            )

        self._pa = pyaudio.PyAudio()
        self._pa_spectrum = pyaudio.PyAudio()
        self._sample_format = pyaudio.paInt16

        self._device_info = self._get_loopback_device()
        _LOG.info(
            "AudioCapture (WASAPI) inicializado. Dispositivo: %s (idx=%d, %dHz, %dch)",
            self._device_info["name"],
            self._device_info["index"],
            int(self._device_info["defaultSampleRate"]),
            min(int(self._device_info["maxInputChannels"]), self._CAPTURE_CHANNELS),
        )
        self._start_spectrum_stream()

    def cleanup(self) -> None:
        """Para o stream de espectro e libera PyAudio."""
        self._spectrum_running = False
        # Aguardar thread do spectrum encerrar antes de fechar o stream
        reader = self._spectrum_reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=2.0)
        self._stop_spectrum_stream()
        # Terminar PyAudio do espectro primeiro (não tem stream aberto mais)
        pa_spectrum = self._pa_spectrum
        self._pa_spectrum = None
        if pa_spectrum is not None:
            try:
                pa_spectrum.terminate()
            except Exception:
                pass
        # Depois o PyAudio principal
        pa = self._pa
        self._pa = None
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass

    # ── Device discovery ────────────────────────────────────────────────────

    def _list_loopback_devices(self) -> list[dict]:
        devices = []
        try:
            for dev in self._pa.get_loopback_device_info_generator():
                devices.append(dev)
        except Exception as exc:
            _LOG.warning("Falha ao listar loopback devices: %s", exc)
        return devices

    def _preferred_loopback_device(self) -> Optional[dict]:
        preferred_idx = self._config.getint("Audio", "capture_device_index", fallback=-1)
        preferred_name = self._config.get("Audio", "capture_device_name", fallback="").strip()

        if preferred_idx < 0 and not preferred_name:
            return None

        devices = self._list_loopback_devices()

        if preferred_idx >= 0:
            for dev in devices:
                if int(dev["index"]) == preferred_idx:
                    return dev
            available = ", ".join(f"{int(d['index'])}: {d['name']}" for d in devices)
            raise AudioCaptureError(
                f"capture_device_index {preferred_idx} inválido. Disponíveis: {available}"
            )

        if preferred_name:
            lower = preferred_name.lower()
            for dev in devices:
                if lower in dev["name"].lower():
                    return dev
            names = "; ".join(d["name"] for d in devices)
            raise AudioCaptureError(
                f"capture_device_name '{preferred_name}' não encontrado. Disponíveis: {names}"
            )

        return None

    def _get_loopback_device(self) -> dict:
        """Resolve o dispositivo de loopback WASAPI a usar."""
        preferred = self._preferred_loopback_device()
        if preferred:
            return preferred

        try:
            wasapi_info = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            raise AudioCaptureError(
                "WASAPI não disponível. Verifique se o driver de áudio suporta WASAPI."
            )

        default_out_idx = wasapi_info.get("defaultOutputDevice", -1)
        if default_out_idx < 0:
            raise AudioCaptureError("Nenhum dispositivo de saída padrão encontrado.")

        default_out = self._pa.get_device_info_by_index(default_out_idx)

        for loopback in self._pa.get_loopback_device_info_generator():
            if loopback["name"].startswith(default_out["name"]):
                return loopback

        devices = self._list_loopback_devices()
        if devices:
            _LOG.warning(
                "Nenhum loopback exato para '%s'. Usando: %s",
                default_out["name"], devices[0]["name"],
            )
            return devices[0]

        raise AudioCaptureError(
            "Nenhum dispositivo de loopback WASAPI encontrado.\n"
            "Verifique se há um dispositivo de saída de áudio ativo no Windows."
        )

    # ── Main capture ────────────────────────────────────────────────────────

    def capture(self, duration: int) -> bytes:
        """Captura áudio do sistema por `duration` segundos. Retorna WAV bytes."""
        with self._lock:
            self._capture_active.set()
            try:
                return self._capture_unsafe(duration)
            finally:
                self._capture_active.clear()

    def _capture_unsafe(self, duration: int) -> bytes:
        if self._pa is None:
            raise AudioCaptureError("AudioCapture não inicializado.")

        dev = self._device_info
        sample_rate = int(dev["defaultSampleRate"])
        channels = min(int(dev["maxInputChannels"]), self._CAPTURE_CHANNELS) or 1

        try:
            stream = self._pa.open(
                format=self._sample_format,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=int(dev["index"]),
                frames_per_buffer=self._FRAMES_PER_BUFFER,
            )
        except OSError as exc:
            raise AudioCaptureError(f"Falha ao abrir stream de captura: {exc}") from exc

        # Latência de buffer do dispositivo WASAPI — usada para compensação de timecode.
        # Para loopback, reflete o tamanho do buffer de renderização (delay entre render → captura).
        input_latency_s: float = 0.0
        with contextlib.suppress(Exception):
            input_latency_s = stream.get_input_latency()

        frames: list[bytes] = []
        total_frames = int(sample_rate * duration)
        captured = 0
        last_read_time: float = 0.0

        try:
            while captured < total_frames:
                if self._shutdown_flag and self._shutdown_flag.is_set():
                    raise AudioCaptureError("Captura interrompida por shutdown")
                to_read = min(self._FRAMES_PER_BUFFER, total_frames - captured)
                try:
                    chunk = stream.read(to_read, exception_on_overflow=False)
                    last_read_time = time.perf_counter()  # imediatamente após cada leitura
                except OSError as exc:
                    _LOG.warning("Overflow durante captura: %s", exc)
                    break
                frames.append(chunk)
                captured += to_read
        finally:
            # Gravar ANTES do cleanup — mede o instante do último sample disponível.
            self._last_audio_end_time = last_read_time if last_read_time > 0 else time.perf_counter()
            self._last_input_latency_s = input_latency_s
            stream.stop_stream()
            stream.close()

        if not frames:
            raise AudioCaptureError(
                "Nenhum frame capturado. Verifique se algo está tocando no sistema."
            )

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(self._pa.get_sample_size(self._sample_format))
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(frames))
        wav_bytes = buf.getvalue()
        _LOG.debug(
            "Captura WASAPI: %d bytes WAV (taxa=%dHz, canais=%d)",
            len(wav_bytes), sample_rate, channels,
        )
        return wav_bytes

    def capture_chunk(self, duration_s: float) -> bytes | None:
        """Captura um chunk de áudio para STT (wrapper de capture)."""
        try:
            duration_int = max(1, int(duration_s))
            return self.capture(duration_int)
        except AudioCaptureError as e:
            _LOG.warning(f"Erro ao capturar chunk STT: {e}")
            return None

    # ── Spectrum capture ────────────────────────────────────────────────────

    def _start_spectrum_stream(self) -> None:
        try:
            dev = self._device_info
            sample_rate = int(dev["defaultSampleRate"])
            channels = min(int(dev["maxInputChannels"]), self._CAPTURE_CHANNELS) or 1

            self._spectrum_stream = self._pa_spectrum.open(
                format=self._sample_format,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=int(dev["index"]),
                frames_per_buffer=self._FRAMES_PER_BUFFER,
            )
            self._spectrum_running = True
            self._spectrum_sample_rate = sample_rate
            self._spectrum_channels = channels

            self._spectrum_reader = threading.Thread(
                target=self._spectrum_reader_loop,
                daemon=True,
                name="SpectrumReader",
            )
            self._spectrum_reader.start()
            _LOG.info("Stream de espectro iniciado (%dHz, %dch)", sample_rate, channels)

        except Exception as exc:
            _LOG.warning("Não foi possível iniciar stream de espectro: %s", exc)
            self._spectrum_stream = None

    def _stop_spectrum_stream(self) -> None:
        stream = self._spectrum_stream
        self._spectrum_stream = None
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    def _spectrum_reader_loop(self) -> None:
        """
        Thread dedicada ao espectro. Nunca para enquanto self._spectrum_running=True.
        Erros de leitura resultam em espera + retry — a thread não sai por conta própria.

        Pipeline:
          int16 PCM estéreo
            → downmix mono
            → ring buffer 2048 amostras
            → FFT com janela de Hann
            → 32 bandas em escala mel (percepção logarítmica)
            → normalização dinâmica com pico decrescente
            → suavização exponencial (ataque rápido / liberação lenta)
            → self._last_spectrum
        """
        if not _NUMPY_AVAILABLE:
            _LOG.warning("NumPy não disponível — espectro de áudio desabilitado.")
            return

        sr = self._spectrum_sample_rate
        ch = self._spectrum_channels
        num_bars = 32
        fpb = self._FRAMES_PER_BUFFER  # 1024 amostras por leitura

        # Janela de FFT: 2048 amostras (~46 ms @ 44100 Hz)
        FFT_N = 2048
        hann = _np.hanning(FFT_N).astype(_np.float32)

        # Escala mel perceptual — nossos ouvidos percebem frequências em escala log
        nyquist = sr / 2.0
        mel_lo = 2595.0 * _np.log10(1.0 + 40.0 / 700.0)          # ~40 Hz (sub-bass)
        mel_hi = 2595.0 * _np.log10(1.0 + min(nyquist * 0.95, 16000.0) / 700.0)
        mel_edges = _np.linspace(mel_lo, mel_hi, num_bars + 1)
        hz_edges = 700.0 * (10.0 ** (mel_edges / 2595.0) - 1.0)
        fft_freqs = _np.fft.rfftfreq(FFT_N, d=1.0 / sr)

        # Índices de banda pré-calculados para evitar busca por frame
        band_lo = [int(_np.searchsorted(fft_freqs, hz_edges[i])) for i in range(num_bars)]
        band_hi = [
            max(band_lo[i] + 1, int(_np.searchsorted(fft_freqs, hz_edges[i + 1])))
            for i in range(num_bars)
        ]

        # Ring buffer mono float32
        ring = _np.zeros(FFT_N, dtype=_np.float32)
        n_filled = 0

        # Normalização dinâmica POR BANDA: cada banda rastreia seu próprio pico.
        # Isso evita que os graves (muito mais energéticos) "abafem" os médios e agudos.
        # Mínimo de 1e-4 evita amplificar o ruído de fundo em bandas sempre silenciosas.
        band_peaks = _np.full(num_bars, 1e-4, dtype=_np.float32)
        # PEAK_DECAY: cada frame a referência decai ~0.3%; ½ vida ≈ 230 frames ≈ 5 s
        PEAK_DECAY = 0.997
        # Sem EMA aqui — suavização visual fica inteiramente no Flutter (60 FPS).
        # Dupla suavização (Python + Flutter) causava efeito "slow motion".

        _LOG.info(
            "SpectrumReader: FFT=%d pts, %d bandas mel [%.0f–%.0f Hz], %d Hz %d ch",
            FFT_N, num_bars, hz_edges[0], hz_edges[-1], sr, ch,
        )

        while self._spectrum_running:
            stream = self._spectrum_stream
            if stream is None:
                time.sleep(0.1)
                continue

            try:
                data = stream.read(fpb, exception_on_overflow=False)
            except Exception as exc:
                _LOG.debug("SpectrumReader: leitura falhou (%s) — aguardando 0.5 s", exc)
                time.sleep(0.5)
                continue

            # Decodificar int16 normalizado, downmix estéreo → mono
            raw = _np.frombuffer(data, dtype="<i2").astype(_np.float32) / 32768.0
            if ch >= 2 and len(raw) >= ch:
                n_frames = len(raw) // ch
                raw = raw[: n_frames * ch].reshape(n_frames, ch).mean(axis=1)

            # Preencher ring buffer com scroll
            n = len(raw)
            if n >= FFT_N:
                ring[:] = raw[-FFT_N:]
                n_filled = FFT_N
            else:
                ring[:-n] = ring[n:]
                ring[-n:] = raw
                n_filled = min(n_filled + n, FFT_N)

            # Aguardar pelo menos metade do buffer para a primeira FFT
            if n_filled < FFT_N // 2:
                continue

            # FFT com janela de Hann — reduz vazamento espectral nas bordas
            fft_mag = _np.abs(_np.fft.rfft(ring * hann))

            # Média de magnitude por banda mel
            bars = _np.array(
                [float(fft_mag[lo:hi].mean()) for lo, hi in zip(band_lo, band_hi)],
                dtype=_np.float32,
            )

            # Normalização dinâmica por banda: cada banda tem seu próprio pico decrescente
            mask_up = bars > band_peaks
            band_peaks = _np.where(mask_up, bars, _np.maximum(band_peaks * PEAK_DECAY, 1e-4))
            normalized = _np.clip(bars / band_peaks, 0.0, 1.0)

            with self._spectrum_buf_lock:
                self._last_spectrum = normalized.tolist()

        _LOG.debug("SpectrumReader encerrado")

    def capture_spectrum(self, num_bars: int = 32) -> list[float]:
        """Retorna o último espectro computado pelo SpectrumReader (thread-safe)."""
        with self._spectrum_buf_lock:
            result = list(self._last_spectrum)
        n = len(result)
        if n == num_bars:
            return result
        if n == 0:
            return [0.0] * num_bars
        # Redimensionar se num_bars diferir (fallback de compatibilidade)
        step = n / num_bars
        return [result[int(i * step)] for i in range(num_bars)]
