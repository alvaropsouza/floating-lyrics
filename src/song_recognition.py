"""
Reconhecimento de músicas via API AudD.
https://audd.io/

Plano gratuito:
  • ~10 reconhecimentos/dia por IP sem chave de API.
  • 100 reconhecimentos/mês com chave gratuita (sem cartão de crédito).
  • Obtenha sua chave em: https://dashboard.audd.io/

Resposta relevante da API:
  result.title      — título da música
  result.artist     — nome do artista
  result.album      — álbum
  result.timecode   — posição na música onde a amostra capturada se encaixa
                      (formato "m:ss" ou "mm:ss"), usada para sincronizar LRC.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time as _time
from dataclasses import dataclass
from typing import Optional, Tuple

import requests

_LOG = logging.getLogger(__name__)


class RecognitionError(Exception):
    """Raised for any song-recognition failure."""


class RateLimitError(RecognitionError):
    """Raised when the recognition API reports a rate-limit / quota error."""


@dataclass
class SongInfo:
    title: str
    artist: str
    album: str = ""
    duration_s: int = 0
    confidence: Optional[float] = None
    # Position (ms) in the song where our audio sample begins — from AudD's
    # `timecode` field.  Used to synchronise LRC lyrics.
    timecode_ms: int = 0


class AudDRecognizer:
    """Identifies songs by sending WAV audio to the AudD API."""

    BASE_URL = "https://api.audd.io/"
    TIMEOUT_S = 30

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key.strip()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "FloatingLyrics/1.0"})

    # ── Public ──────────────────────────────────────────────────────────────

    def recognize(
        self, audio_bytes: bytes, capture_start_time: float
    ) -> Tuple[Optional[SongInfo], float]:
        """
        Send *audio_bytes* (WAV) to AudD for identification.

        Args:
            audio_bytes: WAV data captured from WASAPI loopback.
            capture_start_time: ``time.perf_counter()`` value recorded
                **before** the capture started. Returned unchanged so the
                caller can pass it along for LRC sync calculations.

        Returns:
            ``(SongInfo, capture_start_time)`` on success, or
            ``(None, capture_start_time)`` when the song was not recognised.

        Raises:
            RecognitionError: for network errors or API-level errors.
        """
        payload = self._post(audio_bytes)
        song = self._parse(payload)
        return song, capture_start_time

    # ── Private ─────────────────────────────────────────────────────────────

    def _post(self, audio_bytes: bytes) -> dict:
        data: dict = {"return": "apple_music,spotify"}
        if self.api_key:
            data["api_token"] = self.api_key

        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}

        try:
            resp = self._session.post(
                self.BASE_URL,
                data=data,
                files=files,
                timeout=self.TIMEOUT_S,
            )
            resp.raise_for_status()
        except requests.Timeout:
            _LOG.warning("AudD timeout")
            raise RecognitionError(
                "Timeout ao conectar ao AudD. Verifique sua conexão com a internet."
            )
        except requests.ConnectionError:
            _LOG.warning("AudD sem conexão")
            raise RecognitionError(
                "Sem conexão com o AudD. Verifique sua internet."
            )
        except requests.HTTPError as exc:
            _LOG.error("AudD HTTP %s", exc.response.status_code)
            raise RecognitionError(
                f"Erro HTTP {exc.response.status_code} do AudD."
            ) from exc

        try:
            return resp.json()
        except ValueError:
            raise RecognitionError("Resposta inesperada do AudD (não é JSON).")

    @staticmethod
    def _timecode_to_ms(timecode: str) -> int:
        """Convert AudD timecode string ('m:ss' or 'h:mm:ss') → milliseconds."""
        if not timecode:
            return 0
        parts = timecode.split(":")
        try:
            if len(parts) == 2:
                return (int(parts[0]) * 60 + int(parts[1])) * 1000
            if len(parts) == 3:
                return (
                    int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                ) * 1000
        except (ValueError, IndexError):
            pass
        return 0

    @staticmethod
    def _safe_int(value) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    def _extract_duration_s(self, result: dict) -> int:
        # AudD fields can vary by source; try common places.
        direct = self._safe_int(result.get("duration"))
        if direct > 0:
            return direct

        spotify = result.get("spotify") or {}
        spotify_ms = self._safe_int(spotify.get("duration_ms"))
        if spotify_ms > 0:
            return max(1, spotify_ms // 1000)

        apple = result.get("apple_music") or {}
        apple_ms = self._safe_int(
            apple.get("durationInMillis")
            or apple.get("duration_in_millis")
            or apple.get("duration_ms")
        )
        if apple_ms > 0:
            return max(1, apple_ms // 1000)

        return 0

    @staticmethod
    def _extract_confidence(result: dict) -> Optional[float]:
        # Some responses use `score`; others may expose `confidence`.
        for key in ("score", "confidence"):
            val = result.get(key)
            try:
                if val is not None:
                    return float(val)
            except (TypeError, ValueError):
                continue
        return None

    def _parse(self, payload: dict) -> Optional[SongInfo]:
        if payload.get("status") == "error":
            err = payload.get("error", {})
            code = err.get("error_code", 0)
            msg = err.get("error_message", "Erro desconhecido")
            if code in (901, 902):
                raise RateLimitError(
                    f"Limite da API AudD atingido (erro {code})."
                    " Tentativas pausadas por 30 minutos."
                )
            raise RecognitionError(f"AudD retornou erro {code}: {msg}")

        result = payload.get("result")
        if not result:
            return None

        return SongInfo(
            title=result.get("title", "").strip(),
            artist=result.get("artist", "").strip(),
            album=result.get("album", "").strip(),
            duration_s=self._extract_duration_s(result),
            confidence=self._extract_confidence(result),
            timecode_ms=self._timecode_to_ms(result.get("timecode", "")),
        )

    def __del__(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass


# ── ACRCloud recognizer ──────────────────────────────────────────────────────

class ACRCloudRecognizer:
    """
    Identifies songs using the ACRCloud REST API.

    Free tier: 1000 requests/day.
    Sign up at: https://console.acrcloud.com/

    Required config keys in [ACRCloud]:
      access_key     — your project access key
      access_secret  — your project access secret
      host           — e.g. identify-eu-west-1.acrcloud.com
    """

    TIMEOUT_S = 30

    def __init__(self, access_key: str = "", access_secret: str = "", host: str = "") -> None:
        self.access_key    = access_key.strip()
        self.access_secret = access_secret.strip()
        self.host          = host.strip().rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "FloatingLyrics/1.0"})
        # Compatibility shim so SettingsDialog can access api_key generically.
        self.api_key = access_key

    @property
    def _endpoint(self) -> str:
        return f"https://{self.host}/v1/identify"

    def _sign(self, timestamp: str) -> str:
        string_to_sign = "\n".join([
            "POST",
            "/v1/identify",
            self.access_key,
            "audio",
            "1",
            timestamp,
        ])
        return base64.b64encode(
            hmac.new(
                self.access_secret.encode("ascii"),
                string_to_sign.encode("ascii"),
                digestmod=hashlib.sha1,
            ).digest()
        ).decode("ascii")

    def recognize(
        self, audio_bytes: bytes, capture_start_time: float
    ) -> Tuple[Optional[SongInfo], float]:
        if not self.access_key or not self.access_secret or not self.host:
            raise RecognitionError(
                "ACRCloud não configurado.\n"
                "Preencha access_key, access_secret e host em Configurações."
            )

        timestamp = str(int(_time.time()))
        signature = self._sign(timestamp)

        data = {
            "access_key":        self.access_key,
            "data_type":         "audio",
            "signature_version": "1",
            "signature":         signature,
            "sample_bytes":      str(len(audio_bytes)),
            "timestamp":         timestamp,
        }

        try:
            resp = self._session.post(
                self._endpoint,
                data=data,
                files={"sample": ("audio.wav", audio_bytes, "audio/wav")},
                timeout=self.TIMEOUT_S,
            )
            resp.raise_for_status()
        except requests.Timeout:
            _LOG.warning("ACRCloud timeout")
            raise RecognitionError("Timeout ao conectar ao ACRCloud.")
        except requests.ConnectionError:
            _LOG.warning("ACRCloud sem conexão")
            raise RecognitionError("Sem conexão com o ACRCloud.")
        except requests.HTTPError as exc:
            _LOG.error("ACRCloud HTTP %s", exc.response.status_code)
            raise RecognitionError(f"Erro HTTP {exc.response.status_code} do ACRCloud.") from exc

        try:
            payload = resp.json()
        except ValueError:
            raise RecognitionError("Resposta inesperada do ACRCloud (não é JSON).")

        song = self._parse(payload)
        return song, capture_start_time

    @staticmethod
    def _parse_timecode(m: dict) -> int:
        for key in ("play_offset_ms", "sample_begin_time_offset_ms"):
            val = m.get(key)
            if val is not None:
                try:
                    return max(0, int(float(val)))
                except (TypeError, ValueError):
                    pass
        return 0

    @staticmethod
    def _parse_score(m: dict) -> Optional[float]:
        try:
            return float(m.get("score", 0) or 0)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse(payload: dict) -> Optional[SongInfo]:
        status = payload.get("status", {})
        code   = status.get("code", -1)
        if code == 1001:
            return None
        if code != 0:
            msg = status.get("msg", "Erro desconhecido")
            raise RecognitionError(f"ACRCloud erro {code}: {msg}")

        music_list = (payload.get("metadata") or {}).get("music") or []
        if not music_list:
            return None

        m      = music_list[0]
        title  = m.get("title", "").strip()
        artist = ", ".join(a.get("name", "") for a in (m.get("artists") or [{}]))
        album  = (m.get("album") or {}).get("name", "").strip()

        duration_ms = m.get("duration_ms") or 0
        try:
            duration_s = max(0, int(float(duration_ms)) // 1000)
        except (TypeError, ValueError):
            duration_s = 0

        return SongInfo(
            title=title,
            artist=artist,
            album=album,
            duration_s=duration_s,
            confidence=ACRCloudRecognizer._parse_score(m),
            timecode_ms=ACRCloudRecognizer._parse_timecode(m),
        )

    def __del__(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass
