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
        self._sync_rate: float = 1.0
        self._confident_streak: int = 0
        self._drift_samples: int = 0
        self._raw_abs_ema: float = 0.0
        self._stable_abs_ema: float = 0.0
        self._miss_streak: int = 0

        # Continuous tracking mode: after we lock onto a song, use shorter
        # captures and optional zero pause between cycles for faster updates.
        self._continuous_tracking = self._config.getboolean(
            "Recognition", "continuous_tracking", fallback=True
        )
        self._tracking_capture_duration = max(
            2,
            self._config.getint("Recognition", "tracking_capture_duration", fallback=3),
        )
        self._tracking_interval = self._config.getfloat(
            "Recognition", "tracking_interval", fallback=0.0
        )
        self._miss_reset_threshold = max(
            1,
            self._config.getint("Recognition", "tracking_miss_reset", fallback=3),
        )

        # Timecode stabilisation knobs (all configurable via config.ini).
        self._min_confidence_pct = self._config.getfloat(
            "Recognition", "timecode_min_confidence", fallback=60.0
        )
        self._required_confident_streak = max(
            1,
            self._config.getint("Recognition", "timecode_confident_streak", fallback=2),
        )
        self._alpha = self._config.getfloat("Recognition", "timecode_alpha", fallback=0.18)
        self._beta = self._config.getfloat("Recognition", "timecode_beta", fallback=0.04)
        self._max_jump_ms = self._config.getint("Recognition", "timecode_max_jump_ms", fallback=7000)
        self._log_every_n = max(
            1,
            self._config.getint("Recognition", "timecode_log_every_n", fallback=5),
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
        if self._is_tracking_mode() and self._continuous_tracking:
            interval = max(0.0, self._tracking_interval)
        else:
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
        duration = self._capture_duration_for_cycle()

        # ── 1. Capture ───────────────────────────────────────────────────────
        try:
            mode_label = "(rastreamento)" if self._is_tracking_mode() else ""
            self.status_changed.emit(f"Capturando {duration}s de áudio… {mode_label}".strip())
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
            self._miss_streak += 1
            if self._miss_streak >= self._miss_reset_threshold:
                self._current_song_key = None
                self._sync_song_key = None
                self._confident_streak = 0
            self.song_not_found.emit()
            self.status_changed.emit("Música não reconhecida")
            return

        self._miss_streak = 0

        song_key = (
            song.title.strip().lower(),
            song.artist.strip().lower(),
            song.album.strip().lower(),
        )
        song.timecode_ms = self._stabilize_timecode(
            song_key,
            song.timecode_ms,
            cst,
            song.confidence,
            song.title,
            song.artist,
        )

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

    def _capture_duration_for_cycle(self) -> int:
        base_duration = self._config.getint("Recognition", "capture_duration", fallback=8)
        if self._is_tracking_mode() and self._continuous_tracking:
            return min(max(2, self._tracking_capture_duration), max(2, base_duration))
        return base_duration

    def _is_tracking_mode(self) -> bool:
        return self._current_song_key is not None

    def _stabilize_timecode(
        self,
        song_key: tuple[str, str, str],
        raw_timecode_ms: int,
        capture_start: float,
        confidence: float | None,
        title: str,
        artist: str,
    ) -> int:
        """
        Stabilise timecode for the same song.

        APIs can return jittery offsets between cycles; for lyrics sync we prefer
        continuity.  We predict where playback should be now from the previous
        anchor and only partially apply outlier jumps.
        """
        raw = max(0, int(raw_timecode_ms))
        conf_pct = self._normalize_confidence_pct(confidence)

        # New song: reset anchor and accept API value.
        if self._sync_song_key != song_key:
            self._sync_song_key = song_key
            self._sync_timecode_ms = raw
            self._sync_capture_start = capture_start
            self._sync_rate = 1.0
            self._confident_streak = 1 if conf_pct >= self._min_confidence_pct else 0
            self._drift_samples = 0
            self._raw_abs_ema = 0.0
            self._stable_abs_ema = 0.0
            _LOG.info(
                "SYNC reset | song='%s - %s' raw=%dms conf=%.1f%%",
                title,
                artist,
                raw,
                conf_pct,
            )
            return raw

        elapsed_ms = int(max(0.0, capture_start - self._sync_capture_start) * 1000)
        elapsed_ms = max(1, elapsed_ms)
        expected_ms = int(self._sync_timecode_ms + elapsed_ms * self._sync_rate)
        raw_delta = raw - expected_ms

        if conf_pct >= self._min_confidence_pct:
            self._confident_streak += 1
        else:
            self._confident_streak = 0

        # Confidence gating: until we get enough confident detections in a row,
        # keep continuity and ignore corrections from noisy recognitions.
        allow_correction = self._confident_streak >= self._required_confident_streak
        if not allow_correction:
            stabilized = expected_ms
        else:
            innovation = max(-self._max_jump_ms, min(self._max_jump_ms, raw_delta))
            conf_gain = 0.6 + 0.4 * (conf_pct / 100.0)
            alpha = max(0.0, min(1.0, self._alpha * conf_gain))
            beta = max(0.0, min(0.3, self._beta * conf_gain))

            stabilized = int(expected_ms + alpha * innovation)
            self._sync_rate += beta * (innovation / elapsed_ms)
            self._sync_rate = max(0.97, min(1.03, self._sync_rate))

        stable_delta = stabilized - expected_ms

        stabilized = max(0, stabilized)
        self._sync_timecode_ms = stabilized
        self._sync_capture_start = capture_start

        # Drift metrics: compare raw API drift against stabilized drift.
        self._drift_samples += 1
        raw_abs = abs(raw_delta)
        stable_abs = abs(stable_delta)
        if self._drift_samples == 1:
            self._raw_abs_ema = float(raw_abs)
            self._stable_abs_ema = float(stable_abs)
        else:
            ema_k = 0.2
            self._raw_abs_ema = self._raw_abs_ema * (1 - ema_k) + raw_abs * ema_k
            self._stable_abs_ema = self._stable_abs_ema * (1 - ema_k) + stable_abs * ema_k

        if self._drift_samples % self._log_every_n == 0:
            improvement = self._raw_abs_ema - self._stable_abs_ema
            _LOG.info(
                "SYNC drift | song='%s - %s' conf=%.1f%% streak=%d raw_delta=%+dms stable_delta=%+dms "
                "raw_ema=%.1fms stable_ema=%.1fms gain=%.1fms rate=%.5f",
                title,
                artist,
                conf_pct,
                self._confident_streak,
                raw_delta,
                stable_delta,
                self._raw_abs_ema,
                self._stable_abs_ema,
                improvement,
                self._sync_rate,
            )
        return stabilized

    @staticmethod
    def _normalize_confidence_pct(confidence: float | None) -> float:
        if confidence is None:
            return 0.0
        try:
            value = float(confidence)
        except (TypeError, ValueError):
            return 0.0
        if value <= 1.0:
            value *= 100.0
        return max(0.0, min(100.0, value))
