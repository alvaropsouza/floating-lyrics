#!/usr/bin/env python3
"""Baixa audio com yt-dlp para o dataset training_audio em subpastas por artista/album.

Padrao de destino:
    training_audio/{artist}/{album}/{title}__{timestamp_or_id}.wav

Exemplo:
  python llm-music-api/download_training_audio.py "URL_DA_PLAYLIST" --artist "Kendrick Lamar" --album "GKMC"

Observacoes:
- Se a URL for playlist/album, o yt-dlp baixa todos os itens automaticamente.
- Requer yt-dlp e ffmpeg instalados no sistema.
"""

from __future__ import annotations

import argparse
from collections import Counter
import difflib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from urllib import request as urllib_request
from urllib.error import URLError


def _slug(text: str) -> str:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return "unknown"
    out = []
    for ch in cleaned:
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_"}:
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "unknown"


def _collect_audio_files(root: Path) -> set[Path]:
    exts = {".wav", ".mp3", ".flac", ".m4a", ".ogg"}
    return {p.resolve() for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts}


def _split_title_suffix(stem: str) -> tuple[str, str | None]:
    # Casos comuns de yt-dlp: title__YYYYMMDD_ID ou title_YYYYMMDD_ID
    match = re.match(r"^(?P<title>.+?)(?:__|_)(?P<suffix>\d{8}_[a-zA-Z0-9_-]{6,})$", stem)
    if match:
        return match.group("title"), match.group("suffix")

    # Fallback: remover ID de YouTube no final (11 chars) se existir
    match = re.match(r"^(?P<title>.+?)[_-](?P<suffix>[A-Za-z0-9_-]{11})$", stem)
    if match:
        return match.group("title"), match.group("suffix")

    return stem, None


def _heuristic_clean_title(raw_title: str) -> str:
    title = raw_title.strip()
    title = re.sub(r"^\s*\d{1,2}\s*[-._)]\s*", "", title)
    title = re.sub(r"^\s*track\s*\d+\s*[-._)]\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\[(official|lyrics?|audio|video|hd|4k)[^\]]*\]", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\((official|lyrics?|audio|video|hd|4k)[^\)]*\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(official\s+video|official\s+audio|lyrics?\s+video|audio\s+only)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(remaster(?:ed|izado)?|restaurad[oa]|4k|hd)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(show\s+completo|full\s+album|ao\s+vivo|live)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"(?:__|_)\d{8}_[a-zA-Z0-9_-]{6,}$", "", title)
    title = re.sub(r"[_-][A-Za-z0-9_-]{11}$", "", title)
    title = re.sub(r"\b\d{4}\b", "", title)
    # Remove sufixos artificiais de colisao (_2, -3, etc.), mas preserva partes legitimas como "pt 1".
    title = re.sub(r"(?:[-_ ](?:[2-9]|1\d)){1,3}$", "", title)
    title = title.replace("_", " ").replace("-", " ")
    title = re.sub(r"\s{2,}", " ", title).strip()
    return title or "unknown-title"


def _heuristic_clean_album(raw_album: str, artist_slug: str) -> str:
    album = (raw_album or "").strip()
    if not album:
        return "unknown-album"

    album = album.replace("_", " ").replace("-", " ")
    album = re.sub(r"\b(full\s+album|album\s+completo|official|audio|video|playlist|deluxe|edition)\b", "", album, flags=re.IGNORECASE)
    album = re.sub(r"\b(show\s+completo|remaster(?:ed|izado)?|restaurad[oa]|4k|hd|ao\s+vivo|live)\b", "", album, flags=re.IGNORECASE)
    album = re.sub(r"\b\d{4}\b", "", album)
    album = re.sub(r"\s{2,}", " ", album).strip()

    album_norm = album.lower()
    if re.search(r"acustic[oa].*\bmtv\b|\bmtv\b.*acustic[oa]", album_norm):
        return "acustico-mtv"

    album_slug = _slug(album)
    album_slug = _strip_artist_from_title(album_slug, artist_slug)
    album_slug = re.sub(r"\b(full|album|official|audio|video|playlist|deluxe|edition|show|completo|remaster|remasterizado|restaurado|restaurada|4k|hd|ao|vivo|live)\b", "", album_slug)
    album_slug = re.sub(r"-+", "-", album_slug).strip("-")

    return album_slug or "unknown-album"


def _strip_prefix_tokens(title_slug: str, token_slug: str) -> str:
    if not title_slug or not token_slug:
        return title_slug
    if title_slug == token_slug:
        return title_slug
    for sep in ("-", " "):
        prefix = f"{token_slug}{sep}"
        if title_slug.startswith(prefix):
            return title_slug[len(prefix) :].strip("- ")
    return title_slug


def _strip_artist_from_title(title_slug: str, artist_slug: str) -> str:
    """Remove o nome do artista do titulo quando aparece como token isolado."""
    if not title_slug or not artist_slug:
        return title_slug

    out = title_slug
    # Remove artista no inicio/fim (inclusive repeticoes)
    out = re.sub(rf"^(?:{re.escape(artist_slug)}-)+", "", out)
    out = re.sub(rf"(?:-{re.escape(artist_slug)})+$", "", out)

    # Remove ocorrencias no meio como token isolado: foo-artist-bar -> foo-bar
    pattern_mid = re.compile(rf"(?:(?<=-)|^){re.escape(artist_slug)}(?:(?=-)|$)")
    out = pattern_mid.sub("", out)
    out = re.sub(r"-+", "-", out).strip("-")

    return out


def _strip_suffix_tokens(title_slug: str, token_slug: str) -> str:
    if not title_slug or not token_slug:
        return title_slug
    if title_slug == token_slug:
        return title_slug

    out = title_slug
    for sep in ("-", " "):
        suffix = f"{sep}{token_slug}"
        if out.endswith(suffix):
            out = out[: -len(suffix)].strip("- ")
    return out


def _strip_trailing_noise_tokens(title_slug: str) -> str:
    if not title_slug:
        return title_slug
    noise_tokens = {
        "remaster", "remastered", "remasterizado", "restaurado", "restaurada",
        "4k", "hd", "show", "completo", "full", "album", "live", "ao", "vivo",
        "oficial", "official", "video", "audio",
    }
    parts = [p for p in title_slug.split("-") if p]
    while parts and (parts[-1] in noise_tokens or re.fullmatch(r"\d{4}", parts[-1])):
        parts.pop()
    return "-".join(parts) if parts else title_slug


def _choose_most_common_slug(values: list[str]) -> str | None:
    valid = [v for v in values if v and v not in {"unknown", "unknown-artist", "unknown-album"}]
    if not valid:
        return None
    return Counter(valid).most_common(1)[0][0]


def _infer_group_context(
    group_entries: list[dict],
    llm_cache: dict[tuple[str, str, str], dict | None],
) -> tuple[str | None, str | None]:
    artist_candidates: list[str] = []
    album_candidates: list[str] = []

    for entry in group_entries:
        cleaned = llm_cache.get(entry["cache_key"])
        if not isinstance(cleaned, dict):
            continue
        c_artist = _slug(str(cleaned.get("artist") or ""))
        if c_artist and c_artist not in {"unknown", "unknown-artist"}:
            artist_candidates.append(c_artist)

    raw_artist = str(group_entries[0]["raw_artist"])
    raw_album = str(group_entries[0]["raw_album"])
    parsed_artist, parsed_album = _split_album_context(raw_album)

    canonical_artist = _choose_most_common_slug(artist_candidates)
    if not canonical_artist:
        canonical_artist = _first_valid_slug(
            [parsed_artist, raw_artist],
            invalid={"unknown", "unknown-artist"},
        )

    for entry in group_entries:
        cleaned = llm_cache.get(entry["cache_key"])
        if not isinstance(cleaned, dict):
            continue
        c_album = str(cleaned.get("album") or "").strip()
        if c_album:
            album_candidates.append(_heuristic_clean_album(c_album, canonical_artist or "unknown-artist"))

    if parsed_album:
        album_candidates.append(parsed_album)
    if raw_album:
        album_candidates.append(_heuristic_clean_album(raw_album, canonical_artist or "unknown-artist"))

    canonical_album = _choose_most_common_slug(album_candidates)
    return canonical_artist, canonical_album


def _split_album_context(raw_album: str) -> tuple[str | None, str | None]:
    """Extrai artista/album de nomes como 'Mac Miller - Swimming'."""
    text = (raw_album or "").replace("_", " ").strip()
    if not text:
        return None, None

    match = re.match(r"^(?P<artist>.+?)\s+[-–]\s+(?P<album>.+)$", text)
    if not match:
        return None, None

    artist = _slug(match.group("artist"))
    album = _slug(match.group("album"))
    if not artist or not album:
        return None, None
    return artist, album


def _first_valid_slug(values: list[str | None], invalid: set[str]) -> str | None:
    for value in values:
        if not value:
            continue
        slug = _slug(value)
        if slug and slug not in invalid:
            return slug
    return None


def _resolve_context_artist_album(
    raw_artist: str,
    raw_album: str,
    cleaned: dict | None,
    fixed_artist: str | None,
    fixed_album: str | None,
) -> tuple[str, str]:
    parsed_artist, parsed_album = _split_album_context(raw_album)

    clean_artist = _first_valid_slug(
        [fixed_artist, parsed_artist, raw_artist, (cleaned or {}).get("artist")],
        invalid={"unknown", "unknown-artist"},
    ) or "unknown-artist"

    clean_album = _first_valid_slug(
        [fixed_album, parsed_album],
        invalid={"unknown", "unknown-album"},
    )
    if not clean_album and raw_album and _slug(raw_album) not in {"unknown", "unknown-album"}:
        clean_album = _heuristic_clean_album(raw_album, clean_artist)
    if not clean_album:
        llm_album = (cleaned or {}).get("album")
        if llm_album and str(llm_album).strip():
            clean_album = _heuristic_clean_album(str(llm_album), clean_artist)
    if not clean_album:
        clean_album = "unknown-album"

    return clean_artist or "unknown-artist", clean_album or "unknown-album"


def _choose_best_title_slug(raw_title: str, llm_title: str | None) -> str:
    heuristic = _slug(_heuristic_clean_title(raw_title))
    llm_slug = _slug(llm_title or "") if llm_title else ""

    if not llm_slug or llm_slug in {"unknown", "unknown-title", "unknown-track"}:
        return heuristic

    llm_tokens = [t for t in llm_slug.split("-") if t]
    heuristic_tokens = [t for t in heuristic.split("-") if t]

    # Rejeita resposta LLM curta demais quando a heuristica tem mais informacao.
    if len(llm_tokens) <= 1 and len(heuristic_tokens) >= 3:
        return heuristic

    if len(llm_slug) < 4 and len(heuristic) >= 6:
        return heuristic

    return llm_slug


def _call_cleanup_llm(llm_url: str, raw_title: str, uploader: str, album_hint: str, timeout_s: int) -> dict | None:
    payload = json.dumps(
        {
            "raw_title": raw_title,
            "uploader": uploader,
            "album_hint": album_hint,
        }
    ).encode("utf-8")
    req = urllib_request.Request(
        llm_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(2):
        try:
            with urllib_request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("data") if isinstance(data, dict) else None
        except (URLError, ValueError) as exc:
            if attempt == 0:
                continue
            print(f"Aviso: limpeza LLM indisponivel para '{raw_title}' ({exc}). Usando heuristica/catalogo.")
            return None
    return None


def _batch_cleanup_url(llm_url: str) -> str:
    base = llm_url.rstrip("/")
    if base.endswith("/clean-metadata"):
        return f"{base}/batch"
    return f"{base}/clean-metadata/batch"


def _call_cleanup_llm_batch(llm_url: str, items: list[dict], timeout_s: int) -> list[dict | None]:
    if not items:
        return []

    payload = json.dumps({"items": items}).encode("utf-8")
    req = urllib_request.Request(
        _batch_cleanup_url(llm_url),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (URLError, ValueError) as exc:
        print(f"Aviso: endpoint batch indisponivel ({exc}). Usando modo individual.")
        return [None] * len(items)

    results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(results, list):
        return [None] * len(items)

    out: list[dict | None] = []
    for idx in range(len(items)):
        item = results[idx] if idx < len(results) else None
        if not isinstance(item, dict):
            out.append(None)
            continue
        data = item.get("data")
        out.append(data if isinstance(data, dict) else None)
    return out


def _search_itunes_tracks(artist: str, album: str, timeout_s: int = 10, *, debug: bool = False) -> list[dict]:
    term = f"{artist} {album}".strip()
    if not term:
        return []
    url = (
        "https://itunes.apple.com/search?entity=song&limit=50&term="
        + quote(term)
    )
    if debug:
        print(f"[iTunes/tracks] GET {url}")
    req = urllib_request.Request(url, method="GET")
    try:
        with urllib_request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, ValueError) as exc:
        if debug:
            print(f"[iTunes/tracks] ERRO: {exc}")
        return []

    results = payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(results, list):
        return []
    if debug:
        print(f"[iTunes/tracks] {len(results)} resultado(s) para '{term}'")
        for r in results[:5]:
            print(f"  track='{r.get('trackName')}' artist='{r.get('artistName')}' album='{r.get('collectionName')}'")
    return results


def _search_itunes_albums(artist: str, album: str, timeout_s: int = 10, *, debug: bool = False) -> list[dict]:
    term = f"{artist} {album}".strip()
    if not term:
        return []
    url = (
        "https://itunes.apple.com/search?entity=album&limit=20&term="
        + quote(term)
    )
    if debug:
        print(f"[iTunes/albums] GET {url}")
    req = urllib_request.Request(url, method="GET")
    try:
        with urllib_request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, ValueError) as exc:
        if debug:
            print(f"[iTunes/albums] ERRO: {exc}")
        return []

    results = payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(results, list):
        return []
    if debug:
        print(f"[iTunes/albums] {len(results)} resultado(s) para '{term}'")
        for r in results:
            print(f"  collectionId={r.get('collectionId')} album='{r.get('collectionName')}' artist='{r.get('artistName')}'")
    return results


def _lookup_itunes_album_tracks(collection_id: int, timeout_s: int = 10, *, debug: bool = False) -> list[str]:
    if not collection_id:
        return []
    url = f"https://itunes.apple.com/lookup?id={int(collection_id)}&entity=song"
    if debug:
        print(f"[iTunes/lookup] GET {url}")
    req = urllib_request.Request(url, method="GET")
    try:
        with urllib_request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, ValueError) as exc:
        if debug:
            print(f"[iTunes/lookup] ERRO: {exc}")
        return []

    results = payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(results, list):
        return []

    tracks: list[str] = []
    for item in results:
        if item.get("wrapperType") != "track":
            continue
        name = str(item.get("trackName", "")).strip()
        if name:
            tracks.append(name)
    if debug:
        print(f"[iTunes/lookup] {len(tracks)} faixa(s) para collectionId={collection_id}:")
        for t in tracks:
            print(f"  '{t}'")
    return tracks


def _musicbrainz_release_tracks(artist: str, album: str, timeout_s: int = 12) -> list[str]:
    if not artist or not album:
        return []

    query = quote(f'artist:"{artist}" release:"{album}"')
    search_url = f"https://musicbrainz.org/ws/2/release/?query={query}&fmt=json&limit=5"
    req = urllib_request.Request(
        search_url,
        method="GET",
        headers={"User-Agent": "floating-lyrics/1.0 (metadata-cleanup)"},
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, ValueError):
        return []

    releases = payload.get("releases") if isinstance(payload, dict) else []
    if not isinstance(releases, list) or not releases:
        return []

    # Escolhe release mais parecido com album solicitado.
    album_norm = _slug(album)
    best_release = None
    best_score = -1.0
    for rel in releases:
        title = str(rel.get("title", ""))
        score = difflib.SequenceMatcher(a=album_norm, b=_slug(title)).ratio()
        if score > best_score:
            best_score = score
            best_release = rel

    if not best_release or not best_release.get("id"):
        return []

    rel_id = best_release["id"]
    detail_url = f"https://musicbrainz.org/ws/2/release/{rel_id}?inc=recordings&fmt=json"
    req2 = urllib_request.Request(
        detail_url,
        method="GET",
        headers={"User-Agent": "floating-lyrics/1.0 (metadata-cleanup)"},
    )
    try:
        with urllib_request.urlopen(req2, timeout=timeout_s) as resp:
            detail = json.loads(resp.read().decode("utf-8"))
    except (URLError, ValueError):
        return []

    tracks: list[str] = []
    for medium in detail.get("media", []) or []:
        for tr in medium.get("tracks", []) or []:
            name = str(tr.get("title", "")).strip()
            if name:
                tracks.append(name)
    return tracks


def _reconcile_album_titles_with_catalog(output_root: Path, *, debug: bool = False) -> int:
    """Corrige titulos incompletos/unknown usando tracklist canônica do album."""
    fixes = 0

    artist_dirs = [p for p in output_root.iterdir() if p.is_dir()]
    for artist_dir in artist_dirs:
        album_dirs = [p for p in artist_dir.iterdir() if p.is_dir()]
        for album_dir in album_dirs:
            files = [p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in {".wav", ".mp3", ".flac", ".m4a", ".ogg"}]
            if not files:
                continue

            albums = _search_itunes_albums(artist_dir.name, album_dir.name, debug=debug)
            if not albums:
                continue

            artist_norm = artist_dir.name.replace("-", " ").lower()
            album_norm = album_dir.name.replace("-", " ").lower()

            # Selecionar melhor album pelo nome+artista e buscar tracklist canônica.
            best_album = None
            best_score = -1.0
            for a in albums:
                a_artist = str(a.get("artistName", "")).lower()
                a_name = str(a.get("collectionName", "")).lower()
                score = 0.0
                if artist_norm in a_artist:
                    score += 0.6
                if album_norm in a_name:
                    score += 0.6
                score += difflib.SequenceMatcher(a=album_norm, b=a_name).ratio() * 0.4
                if score > best_score:
                    best_score = score
                    best_album = a

            if not best_album:
                continue

            if debug:
                print(f"[iTunes] Album escolhido: '{best_album.get('collectionName')}' artist='{best_album.get('artistName')}' id={best_album.get('collectionId')} score={best_score:.2f}")
            track_names = _lookup_itunes_album_tracks(int(best_album.get("collectionId", 0)), debug=debug)
            track_slugs: list[str] = []
            for name in track_names:
                slug = _slug(name)
                if slug and slug not in track_slugs:
                    track_slugs.append(slug)

            if not track_slugs:
                mb_tracks = _musicbrainz_release_tracks(artist_dir.name, album_dir.name)
                for name in mb_tracks:
                    slug = _slug(name)
                    if slug and slug not in track_slugs:
                        track_slugs.append(slug)

            if not track_slugs:
                continue

            used = {f.stem for f in files}
            for src in files:
                stem = src.stem
                if stem in track_slugs:
                    continue

                # Priorizar correcao de nomes fracos
                weak_name = stem.startswith("unknown") or len(stem) <= 5 or stem.endswith("-pt")
                if not weak_name:
                    continue

                best = None
                best_score = 0.0
                for cand in track_slugs:
                    if cand in used:
                        continue
                    score = difflib.SequenceMatcher(a=stem, b=cand).ratio()
                    if cand.startswith(stem) or stem.startswith(cand):
                        score += 0.25
                    if score > best_score:
                        best_score = score
                        best = cand

                if not best:
                    continue
                if best_score < 0.40 and not (stem == "unknown-title" and len(track_slugs) > 0):
                    continue

                dst = src.with_name(f"{best}{src.suffix.lower()}")
                if dst.exists() and dst != src:
                    # Se destino ja existe, nao sobrescrever.
                    continue
                src.replace(dst)
                used.discard(stem)
                used.add(best)
                fixes += 1
                print(f"Catalogo corrigiu: {src.name} -> {dst.name}")

    return fixes


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _strip_artificial_suffix(name: str) -> str:
    # Remove sufixos artificiais gerados por colisao (_2, -2, _3...)
    # sem afetar extensao.
    return re.sub(r"([_-])\d+$", "", name)


def _prune_empty_dirs(root: Path) -> int:
    removed = 0
    if not root.exists():
        return removed

    # Remove de baixo para cima para apagar pais que ficaram vazios.
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            if d == root:
                continue
            if not any(d.iterdir()):
                d.rmdir()
                removed += 1
        except OSError:
            continue

    return removed


def _cleanup_new_files(
    output_root: Path,
    new_files: list[Path],
    use_llm_cleanup: bool,
    llm_url: str,
    llm_timeout_s: int,
    fixed_artist: str | None = None,
    fixed_album: str | None = None,
    debug_itunes: bool = False,
) -> None:
    if not new_files:
        return

    print(f"Limpando metadados de {len(new_files)} arquivo(s)...")

    moved = 0
    llm_cache: dict[tuple[str, str, str], dict | None] = {}
    llm_hits = 0
    llm_misses = 0
    entries: list[dict] = []

    for src in new_files:
        raw_artist = src.parent.parent.name if src.parent.parent != output_root else ""
        raw_album = src.parent.name if src.parent != output_root else ""
        raw_title, _ = _split_title_suffix(src.stem)
        cache_key = (raw_title.strip().lower(), raw_artist.strip().lower(), raw_album.strip().lower())
        entries.append(
            {
                "src": src,
                "raw_artist": raw_artist,
                "raw_album": raw_album,
                "raw_title": raw_title,
                "cache_key": cache_key,
                "group_key": (raw_artist.strip().lower(), raw_album.strip().lower()),
            }
        )

    if use_llm_cleanup:
        unique_keys: list[tuple[str, str, str]] = []
        batch_items: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for entry in entries:
            key = entry["cache_key"]
            if key in seen:
                continue
            seen.add(key)
            unique_keys.append(key)
            batch_items.append(
                {
                    "raw_title": entry["raw_title"],
                    "uploader": entry["raw_artist"],
                    "album_hint": entry["raw_album"],
                }
            )

        batch_results = _call_cleanup_llm_batch(
            llm_url=llm_url,
            items=batch_items,
            timeout_s=llm_timeout_s,
        )
        prefetched = 0
        if debug_itunes:
            print(f"[Batch API] Resposta para {len(batch_results)} item(ns):")
            for i, (r, item) in enumerate(zip(batch_results, batch_items)):
                print(f"  [{i}] raw='{item['raw_title']}' -> {r}")
        for idx, key in enumerate(unique_keys):
            result = batch_results[idx] if idx < len(batch_results) else None
            if result is not None:
                llm_cache[key] = result
                prefetched += 1
        if batch_items:
            print(f"Prefetch LLM batch: {prefetched}/{len(batch_items)} item(ns) resolvido(s) em lote.")

    group_context: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    if not fixed_artist and not fixed_album:
        grouped: dict[tuple[str, str], list[dict]] = {}
        for entry in entries:
            grouped.setdefault(entry["group_key"], []).append(entry)
        for group_key, group_entries in grouped.items():
            group_context[group_key] = _infer_group_context(group_entries, llm_cache)

    for entry in entries:
        src = entry["src"]
        raw_artist = entry["raw_artist"]
        raw_album = entry["raw_album"]
        raw_title = entry["raw_title"]

        cleaned = None
        if use_llm_cleanup:
            cache_key = entry["cache_key"]
            if cache_key in llm_cache:
                llm_hits += 1
                cleaned = llm_cache[cache_key]
            else:
                llm_misses += 1
                cleaned = _call_cleanup_llm(
                    llm_url=llm_url,
                    raw_title=raw_title,
                    uploader=raw_artist,
                    album_hint=raw_album,
                    timeout_s=llm_timeout_s,
                )
                llm_cache[cache_key] = cleaned

        group_artist = None
        group_album = None
        if not fixed_artist and not fixed_album:
            group_artist, group_album = group_context.get(entry["group_key"], (None, None))

        clean_title = _choose_best_title_slug(raw_title, (cleaned or {}).get("title"))
        clean_artist, clean_album = _resolve_context_artist_album(
            raw_artist=raw_artist,
            raw_album=raw_album,
            cleaned=cleaned,
            fixed_artist=fixed_artist or group_artist,
            fixed_album=fixed_album or group_album,
        )

        # Evitar nomes com repeticao de artista/album no titulo.
        clean_title = _strip_prefix_tokens(clean_title, clean_artist)
        clean_title = _strip_prefix_tokens(clean_title, clean_album)
        clean_title = _strip_suffix_tokens(clean_title, clean_artist)
        clean_title = _strip_suffix_tokens(clean_title, clean_album)
        clean_title = _strip_artist_from_title(clean_title, clean_artist)
        clean_title = _strip_trailing_noise_tokens(clean_title)
        if not clean_title:
            clean_title = "unknown-title"

        final_name = f"{clean_title}{src.suffix.lower()}"
        dst = output_root / clean_artist / clean_album / final_name
        dst.parent.mkdir(parents=True, exist_ok=True)

        # Se o arquivo atual so difere por sufixo artificial, tentar nome canonico.
        src_stem_clean = _strip_artificial_suffix(src.stem)
        if src_stem_clean == clean_title:
            dst = output_root / clean_artist / clean_album / f"{clean_title}{src.suffix.lower()}"

        if dst.exists() and dst != src:
            # Nao remover arquivos automaticamente: preservar sempre e criar nome unico.
            pass

        if dst.resolve() != src.resolve():
            dst = _unique_path(dst)

        if src.resolve() != dst.resolve():
            src.replace(dst)
            moved += 1
            print(f"Renomeado: {src.name} -> {dst.relative_to(output_root)}")

    removed_dirs = _prune_empty_dirs(output_root)
    if removed_dirs > 0:
        print(f"Pastas vazias removidas: {removed_dirs}")
    print(f"Arquivos reorganizados: {moved}")
    if use_llm_cleanup:
        print(f"Cache LLM: {llm_hits} hit(s), {llm_misses} miss(es), {len(llm_cache)} chave(s).")

    catalog_fixes = _reconcile_album_titles_with_catalog(output_root, debug=debug_itunes)
    if catalog_fixes > 0:
        print(f"Titulos corrigidos via catalogo: {catalog_fixes}")


def build_command(
    url: str,
    output_root: Path,
    artist: str | None,
    album: str | None,
    cookies_from_browser: str | None,
    cookies_file: str | None,
) -> list[str]:
    artist_part = _slug(artist) if artist else "%(uploader)s"
    album_part = _slug(album) if album else "%(playlist_title)s"

    # Padrao final: training_audio/{artist}/{album}/{title}__{upload_date}_{id}.wav
    output_template = str(
        output_root / artist_part / album_part / "%(title)s__%(upload_date)s_%(id)s.%(ext)s"
    )

    cmd = [
        "yt-dlp",
        "--yes-playlist",
        "--extract-audio",
        "--audio-format",
        "wav",
        "--audio-quality",
        "0",
        "--restrict-filenames",
        "-o",
        output_template,
    ]

    if cookies_from_browser:
        cmd.extend(["--cookies-from-browser", cookies_from_browser])
    if cookies_file:
        cmd.extend(["--cookies", cookies_file])

    cmd.append(url)
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="Baixar album/playlist para training_audio em subpastas por artista/album")
    parser.add_argument("url", nargs="?", help="URL do video/playlist/album")
    parser.add_argument(
        "--output-root",
        default=str(Path(__file__).resolve().parent / "training_audio"),
        help="Pasta raiz do dataset (default: llm-music-api/training_audio)",
    )
    parser.add_argument("--artist", default=None, help="Nome fixo do artista para todas as faixas")
    parser.add_argument("--album", default=None, help="Nome fixo do album para todas as faixas")
    parser.add_argument(
        "--no-llm-cleanup",
        action="store_true",
        help="Desativa limpeza via rota LLM (usa apenas heuristica local)",
    )
    parser.add_argument(
        "--llm-url",
        default="http://127.0.0.1:3000/clean-metadata",
        help="URL da rota de limpeza de metadados",
    )
    parser.add_argument(
        "--llm-timeout",
        type=int,
        default=60,
        help="Timeout (segundos) para chamadas da rota LLM",
    )
    parser.add_argument(
        "--clean-existing",
        action="store_true",
        help="Tambem limpa/renomeia arquivos ja existentes no output-root",
    )
    parser.add_argument(
        "--clean-only",
        action="store_true",
        help="Nao baixa nada; apenas limpa/reorganiza arquivos existentes",
    )
    parser.add_argument(
        "--debug-itunes",
        action="store_true",
        help="Imprime respostas brutas do iTunes/MusicBrainz para diagnostico",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Passa --cookies-from-browser para o yt-dlp (ex.: chrome, firefox, edge)",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        help="Caminho para arquivo de cookies no formato Netscape (yt-dlp --cookies)",
    )

    args = parser.parse_args()

    if shutil.which("yt-dlp") is None:
        print("Erro: yt-dlp nao encontrado no PATH.")
        print("Instale com: pip install yt-dlp  (ou via winget/choco/scoop)")
        return 1

    if shutil.which("ffmpeg") is None:
        print("Erro: ffmpeg nao encontrado no PATH.")
        print("Instale ffmpeg para conversao para WAV.")
        return 1

    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    before_files = _collect_audio_files(output_root)

    if not args.clean_only:
        if not args.url:
            print("Erro: informe uma URL ou use --clean-only.")
            return 2

        cmd = build_command(
            url=args.url,
            output_root=output_root,
            artist=args.artist,
            album=args.album,
            cookies_from_browser=args.cookies_from_browser,
            cookies_file=args.cookies,
        )

        print("Baixando para:", output_root)
        print("Comando:", " ".join(cmd[:-1]), "<url>")

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"Falha no download (codigo {exc.returncode}).")
            if args.cookies_from_browser:
                print("Dica: falha ao ler cookies do navegador.")
                print("- Feche totalmente o navegador informado (incluindo processos em segundo plano) e tente novamente.")
                print("- Se persistir, tente outro navegador: --cookies-from-browser edge ou --cookies-from-browser firefox.")
                print("- Alternativa mais estavel: exporte cookies para arquivo e use --cookies <arquivo>.txt.")
            return exc.returncode
    else:
        print("Modo clean-only: pulando download.")

    after_files = _collect_audio_files(output_root)
    new_files = sorted(after_files - before_files)
    files_to_clean = sorted(after_files) if args.clean_existing else new_files

    if args.clean_existing or args.clean_only:
        print(f"Modo clean-existing ativo: processando {len(files_to_clean)} arquivo(s).")
    elif not files_to_clean:
        print("Nenhum arquivo novo detectado para limpeza.")

    _cleanup_new_files(
        output_root=output_root,
        new_files=files_to_clean,
        use_llm_cleanup=not args.no_llm_cleanup,
        llm_url=args.llm_url,
        llm_timeout_s=max(3, args.llm_timeout),
        fixed_artist=args.artist,
        fixed_album=args.album,
        debug_itunes=args.debug_itunes,
    )

    print("Download concluido.")
    print("Padrao salvo: training_audio/{artist}/{album}/{title}__{arquivo}.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
