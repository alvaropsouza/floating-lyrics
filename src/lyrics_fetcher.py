"""
Busca de letras de músicas com fallback automático.

Prioridade:
  1. lrclib.net  — API pública e gratuita, sem chave de API, suporta LRC
                   sincronizado (timestamps por linha).
  2. Musixmatch  — requer chave gratuita (https://developer.musixmatch.com/).
                   Retorna letras sem sincronização.
"""

from __future__ import annotations

import json as _json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import requests

_LOG = logging.getLogger(__name__)
_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "lyrics"
_ARTIST_SPLIT_RE = re.compile(r'[,&/]')  # separadores de artistas compostos
# Marcadores de seção de letra: [Chorus], [Verse 1], [Bridge], etc.
# Linha inteira entre colchetes, sem timestamp numérico (que seria [mm:ss.cc]).
_SECTION_MARKER_RE = re.compile(r'^\[(?!\d+:)[^\]]+\]$')
_LRC_TIMESTAMP_RE = re.compile(r'\[\d{1,3}:\d{2}\.\d{2,3}\]')


def _is_lrc_section_marker(line: str) -> bool:
    """Retorna True se a linha LRC, após remover timestamps, for apenas um marcador de seção."""
    text = _LRC_TIMESTAMP_RE.sub('', line).strip()
    return bool(_SECTION_MARKER_RE.match(text))


@dataclass
class LyricsResult:
    """Holds lyrics returned by any fetcher."""

    lines: list[str]
    synced: bool       # True  → LRC format with timestamps
    raw_lrc: str = ""  # Full LRC text (only when synced=True)



def _safe_json(response: requests.Response) -> object:
    """Parse JSON always as UTF-8, regardless of what requests infers."""
    return _json.loads(response.content.decode("utf-8"))


def _normalize_str(s: str) -> str:
    """NFC-normalize and lowercase for consistent unicode comparison."""
    return unicodedata.normalize("NFC", s).lower()


def _strip_accents(s: str) -> str:
    """Remove combining marks so accented chars compare as plain ASCII."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _slug(s: str) -> str:
    cleaned = _strip_accents(_normalize_str(s)).strip()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
    cleaned = cleaned.strip("-")
    return cleaned or "unknown"


def _safe_future_result(fut) -> Optional["LyricsResult"]:
    """Extrai o resultado de um Future, retornando None em caso de exceção."""
    try:
        return fut.result()
    except Exception:
        return None



class LrcLibFetcher:
    """
    Fetches synced (LRC) or plain lyrics from https://lrclib.net.

    No API key required.  Docs: https://lrclib.net/docs
    """

    BASE_URL = "https://lrclib.net/api"
    TIMEOUT = 5  # Reduzido para evitar bloqueios longos
    _USER_AGENT = "FloatingLyrics/1.0 (https://github.com/floating-lyrics)"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self._USER_AGENT})

    def fetch(
        self,
        title: str,
        artist: str,
        album: str = "",
        duration_s: int = 0,
    ) -> Optional[LyricsResult]:
        """Dispara todas as estratégias de busca em paralelo e retorna o primeiro hit sincronizado."""
        tasks = self._build_parallel_tasks(title, artist, album, duration_s)
        return self._await_best_result(tasks)

    def _build_parallel_tasks(self, title: str, artist: str, album: str, duration_s: int) -> list:
        """Cria e submete todas as tarefas de busca para um ThreadPoolExecutor."""
        duration = max(0, int(duration_s or 0))
        first_artist = _ARTIST_SPLIT_RE.split(artist)[0].strip()
        plain_artist = _strip_accents(artist)
        plain_title = _strip_accents(title)
        candidates = self._duration_candidates(duration)
        base_url, timeout = self.BASE_URL, self.TIMEOUT

        pool = ThreadPoolExecutor(max_workers=8)
        tasks: list = []

        # /get — artista principal, todos os candidatos de duração
        for d in candidates:
            tasks.append(pool.submit(
                self._fetch_one_candidate, base_url, title, artist, album, d, timeout,
            ))
        # /get — primeiro artista (artistas compostos "A, B, C" → "A")
        if first_artist and first_artist.lower() != artist.lower():
            for d in candidates:
                tasks.append(pool.submit(
                    self._fetch_one_candidate, base_url, title, first_artist, album, d, timeout,
                ))
        # /search — query principal: "{artista} {título}"
        tasks.append(pool.submit(
            self._search_one_isolated,
            f"{artist} {title}", title, artist, duration, base_url, timeout,
        ))
        # /search — query sem acentos (diferença NFC/NFD)
        if plain_artist != artist or plain_title != title:
            tasks.append(pool.submit(
                self._search_one_isolated,
                f"{plain_artist} {plain_title}", title, artist, duration, base_url, timeout,
            ))
        # /search — query com primeiro artista
        if first_artist and first_artist.lower() != artist.lower():
            tasks.append(pool.submit(
                self._search_one_isolated,
                f"{first_artist} {title}", title, first_artist, duration, base_url, timeout,
            ))

        _LOG.debug(
            "lrclib: %d tarefas paralelas para '%s' – '%s'",
            len(tasks), title, artist,
        )
        # Registrar shutdown do pool ao encerrar as tarefas via _await_best_result
        tasks.append(pool)  # último elemento é o pool para shutdown
        return tasks

    @staticmethod
    def _await_best_result(tasks: list) -> Optional[LyricsResult]:
        """Aguarda o melhor resultado das tarefas paralelas (sinc > plain)."""
        futures = [t for t in tasks if hasattr(t, 'result')]
        pool_obj = tasks[-1] if tasks and hasattr(tasks[-1], 'shutdown') else None

        best_plain: Optional[LyricsResult] = None
        synced_hit: Optional[LyricsResult] = None
        try:
            for fut in as_completed(futures):
                res = _safe_future_result(fut)
                if res is None:
                    continue
                if res.synced:
                    synced_hit = res
                    for f in futures:
                        f.cancel()
                    break
                if best_plain is None:
                    best_plain = res
        finally:
            if pool_obj is not None:
                pool_obj.shutdown(wait=False, cancel_futures=True)

        return synced_hit if synced_hit is not None else best_plain

    @staticmethod
    def _fetch_one_candidate(base_url: str, title: str, artist: str, album: str, dur: int, timeout: int) -> Optional[LyricsResult]:
        session = requests.Session()
        session.headers.update({"User-Agent": LrcLibFetcher._USER_AGENT})
        params: dict = {"track_name": title, "artist_name": artist}
        if album:
            params["album_name"] = album
        if dur > 0:
            params["duration"] = dur
        try:
            r = session.get(f"{base_url}/get", params=params, timeout=timeout)
            if r.status_code == 200:
                return LrcLibFetcher._parse_response(_safe_json(r))
        except Exception:
            pass
        finally:
            session.close()
        return None

    @staticmethod
    def _search_one_isolated(query: str, title: str, artist: str, duration_s: int, base_url: str, timeout: int) -> Optional[LyricsResult]:
        """Executa uma única query /search com sessão própria (thread-safe)."""
        session = requests.Session()
        session.headers.update({"User-Agent": LrcLibFetcher._USER_AGENT})
        try:
            r = session.get(f"{base_url}/search", params={"q": query}, timeout=timeout)
            r.raise_for_status()
            results = _safe_json(r)
        except (requests.RequestException, ValueError):
            return None
        finally:
            session.close()

        if not isinstance(results, list) or not results:
            return None

        ranked = sorted(
            results,
            key=lambda item: LrcLibFetcher._score_candidate(item, title, artist, duration_s),
            reverse=True,
        )
        for item in ranked:
            parsed = LrcLibFetcher._parse_response(item)
            if parsed is not None:
                return parsed
        return None

    def _parallel_get(
        self,
        title: str,
        artist: str,
        album: str,
        candidates: list[int],
    ) -> Optional[LyricsResult]:
        """Fire all /get duration candidates concurrently; return first synced hit, else first plain."""
        best_plain: Optional[LyricsResult] = None
        base_url, timeout = self.BASE_URL, self.TIMEOUT
        # Limita a 3 workers para evitar rate-limiting
        max_workers = min(3, len(candidates))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._fetch_one_candidate, base_url, title, artist, album, d, timeout): d
                for d in candidates
            }
            for fut in as_completed(futures):
                res = fut.result()
                if res is None:
                    continue
                if res.synced:
                    for f in futures:
                        f.cancel()
                    return res
                if best_plain is None:
                    best_plain = res
        return best_plain

    def _get_by_signature(
        self,
        title: str,
        artist: str,
        album: str,
        duration_s: int,
    ) -> Optional[dict]:
        params: dict = {"track_name": title, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration_s > 0:
            params["duration"] = duration_s

        # Log da requisição
        _LOG.debug(f"🌐 lrclib REQUEST → GET {self.BASE_URL}/get")
        _LOG.debug(f"   Params: {params}")
        
        try:
            r = self._session.get(
                f"{self.BASE_URL}/get", params=params, timeout=self.TIMEOUT
            )
            
            # Log da resposta
            _LOG.debug(f"🌐 lrclib RESPONSE ← Status {r.status_code}")
            if r.status_code == 200:
                _LOG.debug(f"   Body: {r.text[:300]}...")
        except requests.RequestException:
            _LOG.warning("lrclib /get falhou", exc_info=True)
            return None

        if r.status_code != 200:
            return None

        try:
            return _safe_json(r)
        except ValueError:
            return None

    def _search(self, title: str, artist: str, duration_s: int = 0) -> Optional[LyricsResult]:
        """Use the lrclib search endpoint as a second attempt."""
        result = self._search_with_query(f"{artist} {title}", title, artist, duration_s)
        if result is not None:
            return result
        # Fallback: try again with accents stripped (handles NFC/NFD mismatches).
        plain_artist = _strip_accents(artist)
        plain_title  = _strip_accents(title)
        if plain_artist != artist or plain_title != title:
            result = self._search_with_query(f"{plain_artist} {plain_title}", title, artist, duration_s)
            if result is not None:
                return result
        # Fallback: artistas compostos ("A, B, C") — tentar só com o primeiro artista
        first_artist = _ARTIST_SPLIT_RE.split(artist)[0].strip()
        if first_artist and first_artist.lower() != artist.lower():
            result = self._search_with_query(f"{first_artist} {title}", title, first_artist, duration_s)
        return result

    def _search_with_query(self, query: str, title: str, artist: str, duration_s: int) -> Optional[LyricsResult]:
        """Run one lrclib /search query and rank results."""
        # Log da requisição
        _LOG.debug(f"🌐 lrclib REQUEST → GET {self.BASE_URL}/search?q={query}")
        
        try:
            r = self._session.get(
                f"{self.BASE_URL}/search",
                params={"q": query},
                timeout=self.TIMEOUT,
            )
            
            # Log da resposta
            _LOG.debug(f"🌐 lrclib RESPONSE ← Status {r.status_code}")
            _LOG.debug(f"   Body: {r.text[:400]}...")
            
            r.raise_for_status()
            results = _safe_json(r)
        except (requests.RequestException, ValueError):
            return None

        if not isinstance(results, list) or not results:
            return None

        ranked = sorted(
            results,
            key=lambda item: self._score_candidate(item, title, artist, duration_s),
            reverse=True,
        )
        for item in ranked:
            parsed = self._parse_response(item)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _duration_candidates(duration_s: int) -> list[int]:
        """Reduzido de ±2s para ±1s para menos requisições paralelas (5→3)."""
        if duration_s <= 0:
            return [0]
        candidates = [duration_s]
        # Apenas ±1s, não ±2s
        if duration_s > 10:
            candidates.append(duration_s - 1)
            candidates.append(duration_s + 1)
        return candidates

    @staticmethod
    def _score_candidate(item: dict, title: str, artist: str, duration_s: int) -> float:
        track_name = _strip_accents(str(item.get("trackName") or item.get("track_name") or ""))
        artist_name = _strip_accents(str(item.get("artistName") or item.get("artist_name") or ""))
        title_cmp  = _strip_accents(_normalize_str(title))
        artist_cmp = _strip_accents(_normalize_str(artist))
        title_sim  = SequenceMatcher(None, title_cmp,  track_name.lower()).ratio()
        
        # Para artistas compostos (ex: "Snoop Dogg, Wiz Khalifa"),
        # considerar o score mais alto entre a string completa e cada artista individual
        artist_parts = [a.strip() for a in _ARTIST_SPLIT_RE.split(artist_cmp) if a.strip()]
        artist_sim = SequenceMatcher(None, artist_cmp, artist_name.lower()).ratio()
        for part in artist_parts:
            part_sim = SequenceMatcher(None, part, artist_name.lower()).ratio()
            if part_sim > artist_sim:
                artist_sim = part_sim

        duration_sim = 0.0
        cand_duration = item.get("duration")
        try:
            cand_duration = int(cand_duration)
        except (TypeError, ValueError):
            cand_duration = 0
        if duration_s > 0 and cand_duration > 0:
            diff = abs(duration_s - cand_duration)
            duration_sim = max(0.0, 1.0 - diff / 10.0)

        return 0.55 * title_sim + 0.35 * artist_sim + 0.10 * duration_sim

    @staticmethod
    def _parse_response(data: dict) -> Optional[LyricsResult]:
        if data.get("syncedLyrics"):
            lrc = data["syncedLyrics"]
            # Remover linhas que são apenas marcadores de seção ([Chorus], [Verse 1], etc.)
            clean_lines = [ln for ln in lrc.splitlines() if not _is_lrc_section_marker(ln)]
            clean_lrc = "\n".join(clean_lines)
            return LyricsResult(lines=clean_lines, synced=True, raw_lrc=clean_lrc)
        if data.get("plainLyrics"):
            txt = data["plainLyrics"]
            lines = [ln for ln in txt.splitlines() if not _SECTION_MARKER_RE.match(ln.strip())]
            return LyricsResult(lines=lines, synced=False)
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
        # Log da requisição
        api_key_masked = f"{self.api_key[:8]}..." if len(self.api_key) > 8 else "***"
        _LOG.debug(f"🌐 Musixmatch REQUEST → GET {self.BASE_URL}/track.search")
        _LOG.debug(f"   Params: q_track={title}, q_artist={artist}, apikey={api_key_masked}")
        
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
            
            # Log da resposta
            _LOG.debug(f"🌐 Musixmatch RESPONSE ← Status {r.status_code}")
            _LOG.debug(f"   Body: {r.text[:400]}...")
            
            r.raise_for_status()
            tracks = r.json()["message"]["body"]["track_list"]
        except Exception:
            _LOG.warning("Musixmatch track search falhou", exc_info=True)
            return None

        if not tracks:
            return None
        return tracks[0]["track"]["track_id"]

    def _get_lyrics(self, track_id: int) -> Optional[LyricsResult]:
        # Log da requisição
        api_key_masked = f"{self.api_key[:8]}..." if len(self.api_key) > 8 else "***"
        _LOG.debug(f"🌐 Musixmatch REQUEST → GET {self.BASE_URL}/track.lyrics.get")
        _LOG.debug(f"   Params: track_id={track_id}, apikey={api_key_masked}")
        
        try:
            r = self._session.get(
                f"{self.BASE_URL}/track.lyrics.get",
                params={"track_id": track_id, "apikey": self.api_key},
                timeout=self.TIMEOUT,
            )
            
            # Log da resposta
            _LOG.debug(f"🌐 Musixmatch RESPONSE ← Status {r.status_code}")
            _LOG.debug(f"   Body: {r.text[:400]}...")
            
            r.raise_for_status()
            body = r.json()["message"]["body"]["lyrics"]["lyrics_body"]
        except Exception:
            _LOG.warning("Musixmatch get lyrics falhou", exc_info=True)
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


class AudCRLyricsFetcher:
    """
    Fetches plain lyrics using AudCR/AudD findLyrics endpoint.

    Endpoint tested: https://api.audd.io/findLyrics/
    """

    DEFAULT_BASE_URL = "https://api.audd.io/findLyrics/"
    TIMEOUT = 8

    def __init__(self, api_token: str, base_url: str = "") -> None:
        self.api_token = (api_token or "").strip()
        self.base_url = (base_url or self.DEFAULT_BASE_URL).strip() or self.DEFAULT_BASE_URL
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "FloatingLyrics/1.0"})

    def fetch(self, title: str, artist: str) -> Optional[LyricsResult]:
        if not self.api_token:
            return None

        query = f"{artist} {title}".strip()
        try:
            r = self._session.get(
                self.base_url,
                params={
                    "api_token": self.api_token,
                    "q": query,
                },
                timeout=self.TIMEOUT,
            )
            r.raise_for_status()
            data = _safe_json(r)
        except Exception:
            _LOG.warning("AudCR findLyrics falhou", exc_info=True)
            return None

        if not isinstance(data, dict) or data.get("status") != "success":
            return None

        result = data.get("result")
        if isinstance(result, dict):
            candidates = [result]
        elif isinstance(result, list):
            candidates = [item for item in result if isinstance(item, dict)]
        else:
            return None

        if not candidates:
            return None

        target_title = _strip_accents(_normalize_str(title))
        target_artist = _strip_accents(_normalize_str(artist))

        def _candidate_score(item: dict) -> float:
            cand_title = _strip_accents(_normalize_str(str(item.get("title") or "")))
            cand_artist = _strip_accents(_normalize_str(str(item.get("artist") or "")))
            title_sim = SequenceMatcher(None, target_title, cand_title).ratio()
            artist_sim = SequenceMatcher(None, target_artist, cand_artist).ratio()
            return 0.6 * title_sim + 0.4 * artist_sim

        best = max(candidates, key=_candidate_score)
        lyrics = str(best.get("lyrics") or "").strip()
        if not lyrics:
            return None

        lines = [ln.rstrip() for ln in lyrics.splitlines()]
        if not any(ln.strip() for ln in lines):
            return None

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

    Priority default: lrclib.net (synced/plain) → Musixmatch (plain) → AudCR (plain).
    """

    def __init__(self, config) -> None:
        self._lrclib = LrcLibFetcher()
        self._musixmatch = MusixmatchFetcher(
            config.get("API", "musixmatch_api_key", fallback="")
        )
        audcr_base_url = config.get(
            "Lyrics",
            "audcr_endpoint",
            fallback="https://api.audd.io/findLyrics/",
        )
        audcr_token = config.get("API", "audcr_api_key", fallback="") or config.get(
            "API", "audd_api_key", fallback=""
        )
        self._audcr = AudCRLyricsFetcher(audcr_token, base_url=audcr_base_url)
        raw_order = config.get(
            "Lyrics",
            "provider_fallback_order",
            fallback="lrclib,musixmatch,audcr",
        )
        self._provider_order = self._parse_provider_order(raw_order)
        # In-memory cache to avoid repeated remote calls for the same song.
        # Value is Optional[LyricsResult]: None means "already checked, not found".
        self._cache: dict[tuple[str, str, str, int], Optional[LyricsResult]] = {}
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _parse_provider_order(raw_order: str) -> list[str]:
        allowed = {"lrclib", "musixmatch", "audcr"}
        tokens = [token.strip().lower() for token in (raw_order or "").split(",")]
        order: list[str] = []
        for token in tokens:
            if token in allowed and token not in order:
                order.append(token)
        if not order:
            return ["lrclib", "musixmatch", "audcr"]
        return order

    @staticmethod
    def _disk_path(title: str, artist: str, album: str) -> Path:
        artist_dir = _slug(artist) or "unknown-artist"
        album_dir  = _slug(album)  or "unknown-album"
        filename   = f"{_slug(title)}.txt"
        return _CACHE_DIR / artist_dir / album_dir / filename

    @staticmethod
    def _serialize_result(
        title: str,
        artist: str,
        album: str,
        duration_s: int,
        result: LyricsResult,
    ) -> str:
        body = result.raw_lrc if result.synced else "\n".join(result.lines)
        synced = "1" if result.synced else "0"
        return "\n".join(
            [
                f"title={title}",
                f"artist={artist}",
                f"album={album}",
                f"duration_s={max(0, int(duration_s or 0))}",
                f"synced={synced}",
                "---",
                body,
            ]
        )

    @staticmethod
    def _deserialize_result(text: str) -> Optional[LyricsResult]:
        if "\n---\n" not in text:
            return None
        header, body = text.split("\n---\n", 1)
        meta: dict[str, str] = {}
        for line in header.splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            meta[k.strip().lower()] = v.strip()

        synced = meta.get("synced", "0") == "1"
        if synced:
            lrc = body.strip("\n")
            if not lrc:
                return None
            return LyricsResult(lines=lrc.splitlines(), synced=True, raw_lrc=lrc)

        lines = list(body.splitlines())
        if not any(ln.strip() for ln in lines):
            return None
        return LyricsResult(lines=lines, synced=False)

    def _load_disk_cache(self, title: str, artist: str, album: str) -> Optional[LyricsResult]:
        path = self._disk_path(title, artist, album)
        if not path.exists():
            return None
        try:
            parsed = self._deserialize_result(path.read_text(encoding="utf-8"))
            if parsed is None:
                _LOG.warning("Cache de letra inválido, ignorando: %s", path)
                return None
            _LOG.info("Letra carregada do cache em disco: %s", path.relative_to(_CACHE_DIR))
            return parsed
        except Exception:
            _LOG.warning("Falha ao ler cache de letra: %s", path, exc_info=True)
            return None

    def _save_disk_cache(
        self,
        title: str,
        artist: str,
        album: str,
        duration_s: int,
        result: LyricsResult,
    ) -> None:
        path = self._disk_path(title, artist, album)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                self._serialize_result(title, artist, album, duration_s, result),
                encoding="utf-8",
            )
            _LOG.info("Letra salva no cache em disco: %s", path.relative_to(_CACHE_DIR))
        except Exception:
            _LOG.warning("Falha ao salvar cache de letra: %s", path, exc_info=True)

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
            int(round(max(0, int(duration_s or 0)) / 2.0) * 2),
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

        # Cache persistente: se existir TXT salvo para a música, não toca nas APIs.
        cached_disk = self._load_disk_cache(title, artist, album)
        if cached_disk is not None:
            self._cache[key] = cached_disk
            return cached_disk

        result: Optional[LyricsResult] = None
        for provider in self._provider_order:
            if provider == "lrclib":
                result = self._lrclib.fetch(title, artist, album, duration_s)
            elif provider == "musixmatch":
                result = self._musixmatch.fetch(title, artist)
            elif provider == "audcr":
                result = self._audcr.fetch(title, artist)
            else:
                continue

            if result is not None:
                _LOG.info("Letra encontrada via provedor: %s", provider)
                self._cache[key] = result
                self._save_disk_cache(title, artist, album, duration_s, result)
                return result

        # Cache both hits and misses so we don't spam providers.
        self._cache[key] = result
        return result
