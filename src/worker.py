"""
Background worker that drives the capture → recognise → lyrics pipeline.

Runs in a dedicated QThread so the Qt event loop (and hence the UI) is never
blocked.  All communication with the main thread happens via Qt signals, which
are automatically queued across thread boundaries.
"""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

from src.song_recognition import RateLimitError


_LOG = logging.getLogger(__name__)


class RecognitionWorker(QThread):
    """
    Infinite loop:
      1. Capture system audio (WASAPI loopback)
      2. Send to AudD for recognition
      3. Fetch lyrics if the song changed
      4. Emit signals so the UI can update itself

    LRC sync notes
    ~~~~~~~~~~~~~~
    AudD's ``timecode`` field reports **where in the song** the captured audio
    sample came from (e.g. "1:30" means the clip started 90 s into the track).
    We record ``capture_start_time = time.perf_counter()`` **before** starting the
    capture.  The UI then computes the current song position as:

        current_pos_ms = timecode_ms + (time.perf_counter() - capture_start_time) * 1000

    This gives accurate sync without needing any media-player integration.
    """

    # ── Signals (all cross-thread safe) ─────────────────────────────────────
    status_changed   = pyqtSignal(str)
    # title, artist, album
    song_found       = pyqtSignal(str, str, str)
    song_not_found   = pyqtSignal()
    # emitted when a new song is detected and lyrics are about to be fetched
    lyrics_loading   = pyqtSignal()
    # lrc_or_plain_text, is_synced, capture_start_time
    lyrics_ready     = pyqtSignal(str, bool, float)
    lyrics_not_found = pyqtSignal()
    # timecode_ms, capture_start_time
    timecode_updated = pyqtSignal(int, float)
    error_occurred   = pyqtSignal(str)

    def __init__(self, config, audio_capture, recognizer, lyrics_fetcher, parent=None):
        super().__init__(parent)
        self._config        = config
        self._audio         = audio_capture
        self._recognizer    = recognizer
        self._lyrics        = lyrics_fetcher
        self._stop_flag     = False
        self._current_song_key: tuple[str, str, str] | None = None
        self._miss_streak: int = 0
        # Lyrics content cache: key → (content, synced) if found, None if not found.
        self._lyrics_cache: dict[tuple[str, str, str], tuple[str, bool] | None] = {}
        # Rate-limit cooldown.
        self._rate_limit_until: float = 0.0
        self._rate_limit_cooldown_s: float = 30 * 60  # 30 minutes

        self._miss_reset_threshold = max(
            1,
            self._config.getint("Recognition", "tracking_miss_reset", fallback=3),
        )
        # Expose recognizer so MainWindow can update the API key at runtime.
        self.recognizer     = recognizer

    # ── Control ─────────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._stop_flag = True

    # ── Thread entry point ──────────────────────────────────────────────────

    def run(self) -> None:
        self._stop_flag = False
        while not self._stop_flag:
            self._cycle()
            if self._stop_flag:
                break
            self._wait_interval()

    # ── Private helpers ─────────────────────────────────────────────────────

    def _wait_interval(self) -> None:
        """Sleep for 'recognition_interval' seconds, checking the stop flag."""
        interval = float(
            self._config.getint("Recognition", "recognition_interval", fallback=2)
        )
        if interval <= 0:
            return
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            if self._stop_flag:
                return
            time.sleep(0.1)

    def _cycle(self) -> None:
        """Run one complete capture → recognise → lyrics cycle."""
        import time as _t
        duration = self._capture_duration_for_cycle()

        # ── 1. Capture ─────────────────────────────────────────────────────
        try:
            self.status_changed.emit(f"Capturando {duration}s de áudio…")
            capture_start = time.perf_counter()
            audio_bytes = self._audio.capture(duration)
            _LOG.debug("Captura concluída em %.1fs", time.perf_counter() - capture_start)
        except Exception as exc:
            _LOG.error("Erro de captura de áudio", exc_info=True)
            self.error_occurred.emit(f"Captura: {exc}")
            return

        if self._stop_flag:
            return

        # ── 2. Recognise ─────────────────────────────────────────────────────
        now = time.monotonic()
        if now < self._rate_limit_until:
            remaining = int(self._rate_limit_until - now)
            mins, secs = divmod(remaining, 60)
            self.status_changed.emit(
                f"⏸ Limite da API atingido — aguardando {mins}m{secs:02d}s…"
            )
            return

        try:
            self.status_changed.emit("Reconhecendo música…")
            t0 = time.perf_counter()
            song, cst = self._recognizer.recognize(audio_bytes, capture_start)
            _LOG.info("Reconhecimento concluído em %.1fs | encontrado=%s", time.perf_counter() - t0, song is not None)
        except RateLimitError as exc:
            _LOG.warning("Rate limit atingido: %s", exc)
            self._rate_limit_until = time.monotonic() + self._rate_limit_cooldown_s
            self.error_occurred.emit(str(exc))
            return
        except Exception as exc:
            _LOG.error("Erro de reconhecimento", exc_info=True)
            self.error_occurred.emit(f"Reconhecimento: {exc}")
            return

        if self._stop_flag:
            return

        if song is None:
            self._miss_streak += 1
            if self._miss_streak >= self._miss_reset_threshold:
                self._current_song_key = None
            self.song_not_found.emit()
            self.status_changed.emit("Música não reconhecida")
            return

        self._miss_streak = 0

        song_key = (
            song.title.strip().lower(),
            song.artist.strip().lower(),
            song.album.strip().lower(),
        )

        self.song_found.emit(song.title, song.artist, song.album)
        self.status_changed.emit(f"Tocando: {song.title} — {song.artist}")

        # ── 3. Lyrics (only when the song changes) ───────────────────────────
        if song_key == self._current_song_key:
            # Same song — refresh the timecode anchor so the UI stays in sync
            # and the timer chain can restart if it died (e.g. end of song).
            self.timecode_updated.emit(song.timecode_ms, cst)
            return

        self._current_song_key = song_key

        if song_key in self._lyrics_cache:
            # Song seen before — replay cached content without a new fetch.
            cached = self._lyrics_cache[song_key]
            if cached is not None:
                content, synced = cached
                self.lyrics_ready.emit(content, synced, cst)
            else:
                self.lyrics_not_found.emit()
                self.status_changed.emit(f"Letra não encontrada: {song.title}")
            self.timecode_updated.emit(song.timecode_ms, cst)
            return

        # First time seeing this song — show loading placeholder and fetch.
        self.lyrics_loading.emit()

        try:
            self.status_changed.emit("Buscando letra…")
            t0 = time.perf_counter()
            result = self._lyrics.fetch(song.title, song.artist, song.album, song.duration_s)
            _LOG.info("Busca de letra concluída em %.1fs | encontrada=%s", time.perf_counter() - t0, result is not None)
        except Exception as exc:
            _LOG.error("Erro ao buscar letra", exc_info=True)
            self.error_occurred.emit(f"Letra: {exc}")
            # Cache as None so subsequent cycles don't loop back to loading.
            self._lyrics_cache[song_key] = None
            self.lyrics_not_found.emit()
            return

        if self._stop_flag:
            return

        if result is None:
            self.lyrics_not_found.emit()
            self.status_changed.emit(f"Letra não encontrada: {song.title}")
            self._lyrics_cache[song_key] = None
            return

        content = result.raw_lrc if result.synced else "\n".join(result.lines)
        self._lyrics_cache[song_key] = (content, result.synced)
        self.lyrics_ready.emit(content, result.synced, cst)
        self.timecode_updated.emit(song.timecode_ms, cst)
        _LOG.info("SYNC | song='%s - %s' timecode=%dms", song.title, song.artist, song.timecode_ms)
        self.status_changed.emit(f"Tocando: {song.title} — {song.artist}")

    def _capture_duration_for_cycle(self) -> int:
        return self._config.getint("Recognition", "capture_duration", fallback=5)
