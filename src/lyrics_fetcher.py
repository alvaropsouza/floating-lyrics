"""
Busca de letras de músicas com fallback automático.

Prioridade:
  1. lrclib.net  — API pública e gratuita, sem chave de API, suporta LRC
                   sincronizado (timestamps por linha).
  2. Musixmatch  — requer chave gratuita (https://developer.musixmatch.com/).
                   Retorna letras sem sincronização.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests


@dataclass
class LyricsResult:
    """Holds lyrics returned by any fetcher."""

    lines: list[str]
    synced: bool       # True  → LRC format with timestamps
    raw_lrc: str = ""  # Full LRC text (only when synced=True)


# ── lrclib.net ──────────────────────────────────────────────────────────────

class LrcLibFetcher:
    """
    Fetches synced (LRC) or plain lyrics from https://lrclib.net.

    No API key required.  Docs: https://lrclib.net/docs
    """

    BASE_URL = "https://lrclib.net/api"
    TIMEOUT = 10

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "FloatingLyrics/1.0 (https://github.com/floating-lyrics)"}
        )

    def fetch(
        self,
        title: str,
        artist: str,
        album: str = "",
        duration_s: int = 0,
    ) -> Optional[LyricsResult]:
        params: dict = {"track_name": title, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration_s:
            params["duration"] = duration_s

        try:
            r = self._session.get(
                f"{self.BASE_URL}/get", params=params, timeout=self.TIMEOUT
            )
        except requests.RequestException:
            return None

        if r.status_code == 404:
            return self._search(title, artist)
        if r.status_code != 200:
            return None

        try:
            data = r.json()
        except ValueError:
            return None

        return self._parse_response(data)

    def _search(self, title: str, artist: str) -> Optional[LyricsResult]:
        """Use the lrclib search endpoint as a second attempt."""
        try:
            r = self._session.get(
                f"{self.BASE_URL}/search",
                params={"q": f"{artist} {title}"},
                timeout=self.TIMEOUT,
            )
            r.raise_for_status()
            results = r.json()
        except (requests.RequestException, ValueError):
            return None

        if not isinstance(results, list) or not results:
            return None

        return self._parse_response(results[0])

    @staticmethod
    def _parse_response(data: dict) -> Optional[LyricsResult]:
        if data.get("syncedLyrics"):
            lrc = data["syncedLyrics"]
            return LyricsResult(
                lines=lrc.splitlines(), synced=True, raw_lrc=lrc
            )
        if data.get("plainLyrics"):
            txt = data["plainLyrics"]
            return LyricsResult(lines=txt.splitlines(), synced=False)
        return None

    def __del__(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass


# ── Musixmatch ───────────────────────────────────────────────────────────────

class MusixmatchFetcher:
    """
    Fetches plain lyrics from Musixmatch (unsynced).

    Requires a free API key from https://developer.musixmatch.com/.
    """

    BASE_URL = "https://api.musixmatch.com/ws/1.1"
    TIMEOUT = 10

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()
        self._session = requests.Session()

    def fetch(self, title: str, artist: str) -> Optional[LyricsResult]:
        if not self.api_key:
            return None

        track_id = self._find_track_id(title, artist)
        if track_id is None:
            return None
        return self._get_lyrics(track_id)

    def _find_track_id(self, title: str, artist: str) -> Optional[int]:
        try:
            r = self._session.get(
                f"{self.BASE_URL}/track.search",
                params={
                    "q_track": title,
                    "q_artist": artist,
                    "apikey": self.api_key,
                    "s_track_rating": "desc",
                    "page_size": "1",
                    "f_has_lyrics": "1",
                },
                timeout=self.TIMEOUT,
            )
            r.raise_for_status()
            tracks = r.json()["message"]["body"]["track_list"]
        except Exception:
            return None

        if not tracks:
            return None
        return tracks[0]["track"]["track_id"]

    def _get_lyrics(self, track_id: int) -> Optional[LyricsResult]:
        try:
            r = self._session.get(
                f"{self.BASE_URL}/track.lyrics.get",
                params={"track_id": track_id, "apikey": self.api_key},
                timeout=self.TIMEOUT,
            )
            r.raise_for_status()
            body = r.json()["message"]["body"]["lyrics"]["lyrics_body"]
        except Exception:
            return None

        if not body:
            return None

        # Strip Musixmatch's commercial usage footer.
        lines = [
            ln for ln in body.splitlines() if not ln.startswith("****")
        ]
        return LyricsResult(lines=lines, synced=False)

    def __del__(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass


# ── Orchestrator ─────────────────────────────────────────────────────────────

class LyricsFetcher:
    """
    Tries each lyrics source in priority order and returns the first result.

    Priority: lrclib.net (synced) → lrclib.net (plain) → Musixmatch (plain).
    """

    def __init__(self, config) -> None:
        self._lrclib = LrcLibFetcher()
        self._musixmatch = MusixmatchFetcher(
            config.get("API", "musixmatch_api_key", fallback="")
        )
        # In-memory cache to avoid repeated remote calls for the same song.
        # Value is Optional[LyricsResult]: None means "already checked, not found".
        self._cache: dict[tuple[str, str, str, int], Optional[LyricsResult]] = {}

    @staticmethod
    def _cache_key(
        title: str,
        artist: str,
        album: str,
        duration_s: int,
    ) -> tuple[str, str, str, int]:
        return (
            title.strip().lower(),
            artist.strip().lower(),
            album.strip().lower(),
            max(0, int(duration_s or 0)),
        )

    def fetch(
        self,
        title: str,
        artist: str,
        album: str = "",
        duration_s: int = 0,
    ) -> Optional[LyricsResult]:
        key = self._cache_key(title, artist, album, duration_s)
        if key in self._cache:
            return self._cache[key]

        # 1 — lrclib.net (free, supports LRC sync)
        result = self._lrclib.fetch(title, artist, album, duration_s)
        if result is not None:
            self._cache[key] = result
            return result

        # 2 — Musixmatch fallback (plain text)
        result = self._musixmatch.fetch(title, artist)
        # Cache both hits and misses so we don't spam providers.
        self._cache[key] = result
        return result
