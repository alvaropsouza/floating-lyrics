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
import json as _json
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
    TIMEOUT_S = 10  # Reduzido de 30s para failover rápido
    CONNECT_TIMEOUT_S = 5

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key.strip()
        self._session = self._create_optimized_session()

    @staticmethod
    def _create_optimized_session() -> requests.Session:
        """Cria sessão HTTP com keep-alive e connection pooling."""
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        session = requests.Session()
        retry = Retry(
            total=1,
            backoff_factor=0.1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST", "GET"]
        )
        adapter = HTTPAdapter(
            pool_connections=5,
            pool_maxsize=10,
            max_retries=retry
        )
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        session.headers.update({
            "User-Agent": "FloatingLyrics/1.0",
            "Connection": "keep-alive"
        })
        return session

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
                timeout=(self.CONNECT_TIMEOUT_S, self.TIMEOUT_S),
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
            return _json.loads(resp.content.decode("utf-8"))
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

    TIMEOUT_S = 10  # Reduzido de 30s para failover rápido
    CONNECT_TIMEOUT_S = 5

    def __init__(self, access_key: str = "", access_secret: str = "", host: str = "") -> None:
        self.access_key    = access_key.strip()
        self.access_secret = access_secret.strip()
        self.host          = host.strip().rstrip("/")
        # Reusa a sessão otimizada do AudDRecognizer
        self._session = AudDRecognizer._create_optimized_session()
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
                timeout=(self.CONNECT_TIMEOUT_S, self.TIMEOUT_S),
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
            payload = _json.loads(resp.content.decode("utf-8"))
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


class MultiProviderRecognizer:
    """
    Tries multiple recognition providers in alternating rounds.

    With order acrcloud,audd and attempts_per_provider=2, sequence is:
    acrcloud#1 -> audd#1 -> acrcloud#2 -> audd#2
    """

    def __init__(
        self,
        audd: AudDRecognizer,
        acrcloud: ACRCloudRecognizer,
        order: str = "acrcloud,audd",
        attempts_per_provider: int = 2,
    ) -> None:
        self.audd = audd
        self.acrcloud = acrcloud
        self._order = self._parse_order(order)
        self._attempts_per_provider = max(1, int(attempts_per_provider or 2))
        self._fresh_capture_callback = None  # Callback para capturar áudio fresco

    @staticmethod
    def _parse_order(order: str) -> list[str]:
        raw = [p.strip().lower() for p in (order or "").split(",") if p.strip()]
        filtered = [p for p in raw if p in ("acrcloud", "audd")]
        if len(filtered) != 2:
            return ["acrcloud", "audd"]
        if filtered[0] == filtered[1]:
            return ["acrcloud", "audd"]
        return filtered

    def configure_fallback(self, order: str, attempts_per_provider: int) -> None:
        self._order = self._parse_order(order)
        self._attempts_per_provider = max(1, int(attempts_per_provider or 2))

    def set_fresh_capture_callback(self, callback) -> None:
        """
        Define callback para capturar áudio fresco a cada tentativa.
        
        O callback deve retornar: (audio_bytes, capture_start_time)
        Se None, usa o mesmo áudio para todas as tentativas (comportamento antigo).
        """
        self._fresh_capture_callback = callback

    def update_credentials(
        self,
        audd_api_key: str,
        acr_access_key: str,
        acr_access_secret: str,
        acr_host: str,
    ) -> None:
        self.audd.api_key = (audd_api_key or "").strip()
        self.acrcloud.access_key = (acr_access_key or "").strip()
        self.acrcloud.access_secret = (acr_access_secret or "").strip()
        self.acrcloud.host = (acr_host or "").strip().rstrip("/")

    def _provider_obj(self, name: str):
        if name == "audd":
            return self.audd
        return self.acrcloud

    def _run_single_attempt(
        self,
        provider_name: str,
        audio_bytes: bytes,
        capture_start_time: float,
        provider_attempt: int,
    ) -> tuple[str, Optional[SongInfo], float, Optional[Exception]]:
        provider = self._provider_obj(provider_name)
        try:
            song, cst = provider.recognize(audio_bytes, capture_start_time)
        except RateLimitError as exc:
            _LOG.warning(
                "Rate-limit no provedor %s (tentativa %d/%d do provedor)",
                provider_name,
                provider_attempt,
                self._attempts_per_provider,
            )
            return "rate_limit", None, capture_start_time, exc
        except RecognitionError as exc:
            _LOG.warning(
                "Falha de reconhecimento no provedor %s (tentativa %d/%d do provedor): %s",
                provider_name,
                provider_attempt,
                self._attempts_per_provider,
                exc,
            )
            return "error", None, capture_start_time, exc

        if song is None:
            _LOG.info(
                "Sem match no provedor %s (tentativa %d/%d do provedor)",
                provider_name,
                provider_attempt,
                self._attempts_per_provider,
            )
            return "miss", None, cst, None
        return "hit", song, cst, None

    def _recognize_with_fallback(
        self, audio_bytes: bytes, capture_start_time: float
    ) -> tuple[Optional[SongInfo], float, Optional[RateLimitError], Optional[RecognitionError], bool]:
        last_rate_limit: Optional[RateLimitError] = None
        last_error: Optional[RecognitionError] = None
        had_clean_miss = False

        rate_limited_providers: set[str] = set()
        providers_count = len(self._order)
        total_slots = providers_count * self._attempts_per_provider

        attempt_plan = [
            (round_idx + 1, provider_name)
            for round_idx in range(self._attempts_per_provider)
            for provider_name in self._order
        ]

        for slot, (provider_attempt, provider_name) in enumerate(attempt_plan, start=1):
            if provider_name in rate_limited_providers:
                _LOG.debug(
                    "Pulando %s por rate-limit já detectado (rodada %d/%d)",
                    provider_name,
                    provider_attempt,
                    self._attempts_per_provider,
                )
                continue

            # Capturar áudio fresco se callback estiver configurado (exceto primeira tentativa)
            if self._fresh_capture_callback is not None and slot > 1:
                _LOG.info("🎵 Capturando trecho FRESCO de áudio para tentativa %d/%d", slot, total_slots)
                try:
                    audio_bytes, capture_start_time = self._fresh_capture_callback()
                    if audio_bytes is None or len(audio_bytes) == 0:
                        _LOG.warning("Captura fresca retornou áudio vazio, pulando tentativa")
                        continue
                except Exception as exc:
                    _LOG.error(f"Erro ao capturar áudio fresco: {exc}", exc_info=True)
                    # Continua com o áudio anterior se falhar

            _LOG.info(
                "Tentativa de reconhecimento %d/%d | provedor=%s | tentativa_provedor=%d/%d",
                slot,
                total_slots,
                provider_name,
                provider_attempt,
                self._attempts_per_provider,
            )

            status, song, cst, err = self._run_single_attempt(
                provider_name, audio_bytes, capture_start_time, provider_attempt
            )
            finished, song, cst, last_rate_limit, last_error, had_clean_miss = self._handle_attempt_result(
                status=status,
                provider_name=provider_name,
                slot=slot,
                total_slots=total_slots,
                song=song,
                cst=cst,
                err=err,
                rate_limited_providers=rate_limited_providers,
                providers_count=providers_count,
                last_rate_limit=last_rate_limit,
                last_error=last_error,
                had_clean_miss=had_clean_miss,
            )
            if finished and song is not None:
                return song, cst, last_rate_limit, last_error, had_clean_miss

        return None, capture_start_time, last_rate_limit, last_error, had_clean_miss

    def _handle_attempt_result(
        self,
        *,
        status: str,
        provider_name: str,
        slot: int,
        total_slots: int,
        song: Optional[SongInfo],
        cst: float,
        err: Optional[Exception],
        rate_limited_providers: set[str],
        providers_count: int,
        last_rate_limit: Optional[RateLimitError],
        last_error: Optional[RecognitionError],
        had_clean_miss: bool,
    ) -> tuple[bool, Optional[SongInfo], float, Optional[RateLimitError], Optional[RecognitionError], bool]:
        if status == "hit" and song is not None:
            _LOG.info(
                "Música reconhecida via %s na tentativa global %d/%d",
                provider_name,
                slot,
                total_slots,
            )
            return True, song, cst, last_rate_limit, last_error, had_clean_miss

        if status == "miss":
            return False, song, cst, last_rate_limit, last_error, True

        if status == "rate_limit":
            rate_limited_providers.add(provider_name)
            if isinstance(err, RateLimitError):
                last_rate_limit = err
            if len(rate_limited_providers) == providers_count:
                _LOG.warning("Todos os provedores foram rate-limited nesta sequência")
            return False, song, cst, last_rate_limit, last_error, had_clean_miss

        if isinstance(err, RecognitionError):
            last_error = err
        return False, song, cst, last_rate_limit, last_error, had_clean_miss

    def recognize(
        self, audio_bytes: bytes, capture_start_time: float
    ) -> Tuple[Optional[SongInfo], float]:
        _LOG.info(
            "Fallback de reconhecimento iniciado | ordem=%s | tentativas_por_provedor=%d",
            " -> ".join(self._order),
            self._attempts_per_provider,
        )

        song, cst, last_rate_limit, last_error, had_clean_miss = self._recognize_with_fallback(
            audio_bytes,
            capture_start_time,
        )
        if song is not None:
            return song, cst

        if had_clean_miss:
            _LOG.info("Reconhecimento finalizado sem match após alternar entre provedores")
            return None, capture_start_time
        if last_rate_limit is not None:
            raise last_rate_limit
        if last_error is not None:
            raise last_error
        raise RecognitionError("Nenhum provedor de reconhecimento disponível.")
