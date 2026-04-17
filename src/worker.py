"""
Background worker that drives the capture → recognise → lyrics pipeline.

Runs in a dedicated QThread so the Qt event loop (and hence the UI) is never
blocked.  All communication with the main thread happens via Qt signals, which
are automatically queued across thread boundaries.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QThread, pyqtSignal


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
        # Rolling anchor used to smooth noisy/late timecode updates.
        self._sync_song_key: tuple[str, str, str] | None = None
        self._sync_timecode_ms: int = 0
        self._sync_capture_start: float = 0.0
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
        interval = self._config.getint("Recognition", "recognition_interval", fallback=2)
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            if self._stop_flag:
                return
            time.sleep(0.1)

    def _cycle(self) -> None:
        """Run one complete capture → recognise → lyrics cycle."""
        duration = self._config.getint("Recognition", "capture_duration", fallback=8)

        # ── 1. Capture ───────────────────────────────────────────────────────
        try:
            self.status_changed.emit(f"Capturando {duration}s de áudio…")
            # perf_counter gives sub-millisecond monotonic resolution on Windows.
            # We record the start *before* capture so the anchor correctly
            # represents the song position at the beginning of the captured clip.
            capture_start = time.perf_counter()
            audio_bytes = self._audio.capture(duration)
        except Exception as exc:
            self.error_occurred.emit(f"Captura: {exc}")
            return

        if self._stop_flag:
            return

        # ── 2. Recognise ─────────────────────────────────────────────────────
        try:
            self.status_changed.emit("Reconhecendo música…")
            song, cst = self._recognizer.recognize(audio_bytes, capture_start)
        except Exception as exc:
            self.error_occurred.emit(f"Reconhecimento: {exc}")
            return

        if self._stop_flag:
            return

        if song is None:
            self.song_not_found.emit()
            self.status_changed.emit("Música não reconhecida")
            return

        song_key = (
            song.title.strip().lower(),
            song.artist.strip().lower(),
            song.album.strip().lower(),
        )
        song.timecode_ms = self._stabilize_timecode(song_key, song.timecode_ms, cst)

        self.song_found.emit(song.title, song.artist, song.album)
        self.timecode_updated.emit(song.timecode_ms, cst)
        self.status_changed.emit(f"Tocando: {song.title} — {song.artist}")

        # ── 3. Lyrics (only when the song changes) ───────────────────────────
        if song_key == self._current_song_key:
            # Same song — re-sync via the new timecode already emitted above.
            return

        self._current_song_key = song_key

        try:
            self.status_changed.emit("Buscando letra…")
            result = self._lyrics.fetch(song.title, song.artist, song.album, song.duration_s)
        except Exception as exc:
            self.error_occurred.emit(f"Letra: {exc}")
            return

        if self._stop_flag:
            return

        if result is None:
            self.lyrics_not_found.emit()
            self.status_changed.emit(f"Letra não encontrada: {song.title}")
            return

        content = result.raw_lrc if result.synced else "\n".join(result.lines)
        self.lyrics_ready.emit(content, result.synced, cst)
        # Re-emit timecode so the lyrics window can start syncing immediately.
        self.timecode_updated.emit(song.timecode_ms, cst)
        self.status_changed.emit(f"Tocando: {song.title} — {song.artist}")

    def _stabilize_timecode(
        self,
        song_key: tuple[str, str, str],
        raw_timecode_ms: int,
        capture_start: float,
    ) -> int:
        """
        Stabilise timecode for the same song.

        APIs can return jittery offsets between cycles; for lyrics sync we prefer
        continuity.  We predict where playback should be now from the previous
        anchor and only partially apply outlier jumps.
        """
        raw = max(0, int(raw_timecode_ms))

        # New song: reset anchor and accept API value.
        if self._sync_song_key != song_key:
            self._sync_song_key = song_key
            self._sync_timecode_ms = raw
            self._sync_capture_start = capture_start
            return raw

        elapsed_ms = int(max(0.0, capture_start - self._sync_capture_start) * 1000)
        expected_ms = self._sync_timecode_ms + elapsed_ms
        delta = raw - expected_ms

        if abs(delta) <= 1500:
            stabilized = raw
        elif abs(delta) <= 6000:
            # Partial correction: keeps continuity while still following drift.
            stabilized = expected_ms + int(delta * 0.25)
        else:
            # Large jump likely means wrong offset from recognition.
            stabilized = expected_ms

        stabilized = max(0, stabilized)
        self._sync_timecode_ms = stabilized
        self._sync_capture_start = capture_start
        return stabilized
