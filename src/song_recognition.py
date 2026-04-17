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

from dataclasses import dataclass, field
from typing import Optional, Tuple

import requests


class RecognitionError(Exception):
    """Raised for any song-recognition failure."""


@dataclass
class SongInfo:
    title: str
    artist: str
    album: str = ""
    duration_s: int = 0
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
            raise RecognitionError(
                "Timeout ao conectar ao AudD. Verifique sua conexão com a internet."
            )
        except requests.ConnectionError:
            raise RecognitionError(
                "Sem conexão com o AudD. Verifique sua internet."
            )
        except requests.HTTPError as exc:
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

    def _parse(self, payload: dict) -> Optional[SongInfo]:
        if payload.get("status") == "error":
            err = payload.get("error", {})
            code = err.get("error_code", 0)
            msg = err.get("error_message", "Erro desconhecido")
            if code == 901:
                raise RecognitionError(
                    "Limite da API AudD atingido.\n"
                    "Obtenha uma chave gratuita em https://dashboard.audd.io/"
                )
            raise RecognitionError(f"AudD retornou erro {code}: {msg}")

        result = payload.get("result")
        if not result:
            return None

        return SongInfo(
            title=result.get("title", "").strip(),
            artist=result.get("artist", "").strip(),
            album=result.get("album", "").strip(),
            timecode_ms=self._timecode_to_ms(result.get("timecode", "")),
        )

    def __del__(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass
