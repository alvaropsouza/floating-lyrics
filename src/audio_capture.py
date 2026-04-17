"""
Captura de áudio via WASAPI loopback (saída do sistema, não microfone).

Dependência: pyaudiowpatch — fork do PyAudio com suporte nativo a WASAPI
loopback no Windows.  Instale com:  pip install pyaudiowpatch
"""

import io
import logging
import math
import struct
import wave
from typing import Optional

_LOG = logging.getLogger(__name__)

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
        self._pa: Optional[object] = None

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
        count = len(raw_bytes) // 2
        if count == 0:
            return 0.0
        shorts = struct.unpack_from(f"<{count}h", raw_bytes)
        return math.sqrt(sum(s * s for s in shorts) / count)

    @staticmethod
    def _normalize(raw_bytes: bytes) -> bytes:
        """
        Scale 16-bit PCM to full dynamic range so recognition APIs receive
        audio at maximum amplitude regardless of the current system volume.

        If the audio is already at or near peak (peak >= 28000) it is returned
        unchanged to avoid integer overflow artifacts.
        """
        count = len(raw_bytes) // 2
        if count == 0:
            return raw_bytes
        fmt = f"<{count}h"
        samples = struct.unpack_from(fmt, raw_bytes)
        peak = max(abs(s) for s in samples)
        if peak < 10:
            # Effectively silent — nothing to normalize.
            return raw_bytes
        if peak >= 28000:
            # Already loud enough; skip to avoid clipping.
            return raw_bytes
        gain = 32767.0 / peak
        normalized = bytes(struct.pack(fmt, *(max(-32768, min(32767, int(s * gain))) for s in samples)))
        return normalized

    # ── Capture ─────────────────────────────────────────────────────────────

    def capture(self, duration: int) -> bytes:
        """
        Record *duration* seconds of system audio via WASAPI loopback.

        Returns:
            WAV-formatted bytes suitable for sending to a recognition API.

        Raises:
            AudioCaptureError: if the capture device is unavailable, no
                audio is detected (silence), or any other recording error.
        """
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
        try:
            total_chunks = int((sample_rate / read_chunk) * duration)
            for _ in range(total_chunks):
                data = stream.read(read_chunk, exception_on_overflow=False)
                frames.append(data)
        except OSError as exc:
            raise AudioCaptureError(f"Erro durante a gravação: {exc}") from exc
        finally:
            stream.stop_stream()
            stream.close()

        raw_audio = b"".join(frames)

        # ── Silence check (before normalization, on raw signal) ─────────────
        # Use a very low threshold here — just enough to reject true silence.
        # The actual normalization will happen next, so the API always gets
        # a loud signal regardless of system volume.
        raw_rms = self._rms(raw_audio)
        _LOG.debug("RMS do áudio bruto: %.1f", raw_rms)
        if raw_rms < 5:
            raise AudioCaptureError(
                f"Nenhum áudio detectado no sistema (RMS = {raw_rms:.1f}).\n"
                "Verifique se algo está tocando."
            )

        # ── Normalize to full amplitude for better recognition ───────────────
        raw_audio = self._normalize(raw_audio)

        # ── Encode as WAV ───────────────────────────────────────────────────
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(self._pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(sample_rate)
            wf.writeframes(raw_audio)

        return buf.getvalue()
