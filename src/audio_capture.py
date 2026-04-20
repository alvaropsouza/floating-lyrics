"""
Captura de áudio via PyAudioWPatch + WASAPI loopback (saída do sistema, não microfone).

Dependência: pyaudiowpatch
  pip install pyaudiowpatch

Funciona exclusivamente no Windows via WASAPI loopback.
"""

import io
import logging
import threading
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

    # ── Lifecycle ───────────────────────────────────────────────────────────

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

        frames: list[bytes] = []
        total_frames = int(sample_rate * duration)
        captured = 0

        try:
            while captured < total_frames:
                if self._shutdown_flag and self._shutdown_flag.is_set():
                    raise AudioCaptureError("Captura interrompida por shutdown")
                to_read = min(self._FRAMES_PER_BUFFER, total_frames - captured)
                try:
                    chunk = stream.read(to_read, exception_on_overflow=False)
                except OSError as exc:
                    _LOG.warning("Overflow durante captura: %s", exc)
                    break
                frames.append(chunk)
                captured += to_read
        finally:
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
        max_bytes = int(
            self._spectrum_sample_rate
            * (self._SPECTRUM_BUFFER_MS / 1000.0)
            * 2
            * self._spectrum_channels
        )

        while self._spectrum_running:
            stream = self._spectrum_stream
            if stream is None:
                break
            try:
                data = stream.read(self._FRAMES_PER_BUFFER, exception_on_overflow=False)
                with self._spectrum_buf_lock:
                    combined = self._spectrum_buf + data
                    self._spectrum_buf = combined[-max_bytes:]
            except Exception:
                break

        _LOG.debug("SpectrumReader encerrado")

    def capture_spectrum(self, duration_ms: int = 100, num_bars: int = 32) -> list[float]:
        """Retorna espectro de frequências calculado a partir do buffer PCM acumulado."""
        acquired = self._spectrum_lock.acquire(blocking=False)
        if not acquired:
            return self._last_spectrum.copy()

        try:
            with self._spectrum_buf_lock:
                buf = self._spectrum_buf

            if not buf or not _NUMPY_AVAILABLE:
                return [0.0] * num_bars

            samples = _np.frombuffer(buf, dtype="<i2").astype(_np.float32)
            if samples.size < num_bars * 2:
                return [0.0] * num_bars

            fft_data = _np.abs(_np.fft.rfft(samples))
            spectrum_size = max(1, int(len(fft_data) * 0.66))
            fft_data = fft_data[:spectrum_size]

            bars = []
            for i in range(num_bars):
                start_idx = int((i / num_bars) ** 1.3 * spectrum_size)
                end_idx = max(start_idx + 1, int(((i + 1) / num_bars) ** 1.3 * spectrum_size))
                band_avg = float(_np.mean(fft_data[start_idx:end_idx]))
                if i > num_bars * 0.7:
                    boost = 1.0 + (i - num_bars * 0.7) / (num_bars * 0.3) * 2.0
                    band_avg *= boost
                bars.append(band_avg)

            max_raw = max(bars) if bars else 0.0
            if max_raw < 1000:
                self._last_spectrum = [0.0] * num_bars
                return self._last_spectrum.copy()

            result = [
                min(1.0, b / max_raw * 1.5) if b > 0.10 else 0.0
                for b in bars
            ]
            self._last_spectrum = result
            return result

        except Exception as exc:
            _LOG.debug("Erro ao computar espectro: %s", exc)
            return [0.0] * num_bars
        finally:
            self._spectrum_lock.release()
