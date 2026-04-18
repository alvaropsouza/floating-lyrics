"""
Background worker that drives the capture → recognise → lyrics pipeline.

Runs in a dedicated QThread so the Qt event loop (and hence the UI) is never
blocked.  All communication with the main thread happens via Qt signals, which
are automatically queued across thread boundaries.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from pathlib import Path

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
        # Tracking state to avoid unnecessary API calls.
        self._last_recognition_time: float = 0.0
        self._current_song_duration_s: float = 0.0
        self._new_song_attempts: int = 0
        self._new_song_cooldown_until: float = 0.0
        self._last_training_save_by_song: dict[tuple[str, str, str], float] = {}

        self._miss_reset_threshold = max(
            1,
            self._config.getint("Recognition", "tracking_miss_reset", fallback=3),
        )
        # Expose recognizer so MainWindow can update the API key at runtime.
        self.recognizer     = recognizer
        
        # Configurar callback de captura fresca no recognizer
        self._recognizer.set_fresh_capture_callback(self._capture_fresh_audio)

    @staticmethod
    def _sanitize_part(value: str, fallback: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return fallback
        normalized = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
        normalized = normalized.lower()
        normalized = re.sub(r"\s+", "-", normalized)
        normalized = re.sub(r"[^a-z0-9._-]", "", normalized)
        normalized = normalized.strip("-._")
        return normalized or fallback

    def _save_training_audio_if_needed(self, song, song_key: tuple[str, str, str], audio_bytes: bytes) -> None:
        if not self._config.getboolean("Recognition", "save_audio_for_training", fallback=False):
            return

        cooldown_s = max(
            0,
            self._config.getint("Recognition", "training_audio_same_song_cooldown_s", fallback=90),
        )
        now = time.time()
        last = self._last_training_save_by_song.get(song_key, 0.0)
        if cooldown_s > 0 and (now - last) < cooldown_s:
            return

        artist = self._sanitize_part(song.artist, "unknown-artist")
        title = self._sanitize_part(song.title, "unknown-title")
        album = self._sanitize_part(song.album, "unknown-album")
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        filename = f"{artist}__{title}__{album}__{stamp}.wav"

        target_dir = Path(__file__).resolve().parent.parent / "llm-music-api" / "training_audio"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename

        try:
            target_path.write_bytes(audio_bytes)
            self._last_training_save_by_song[song_key] = now
            _LOG.info("🎓 Trecho salvo para treino: %s", target_path.name)
        except Exception as exc:
            _LOG.warning("Falha ao salvar trecho para treino: %s", exc)

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

    def _capture_fresh_audio(self) -> tuple[bytes, float]:
        """
        Captura um trecho FRESCO de áudio para uma nova tentativa de reconhecimento.
        
        Retorna: (audio_bytes, capture_start_time)
        """
        _LOG.debug("Capturando trecho fresco de áudio...")
        capture_start = time.perf_counter()
        duration = self._config.getint("Capture", "capture_duration", fallback=10)
        audio_data = self._audio.capture(duration)
        return audio_data, capture_start

    def _wait_interval(self) -> None:
        """Sleep for 'recognition_interval' seconds, checking the stop flag."""
        interval = self._interval_for_cycle()
        if interval <= 0:
            return
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            if self._stop_flag:
                return
            time.sleep(0.1)

    def _new_song_limit_reached(self) -> bool:
        if self._current_song_key is not None:
            return False
        now = time.monotonic()
        if now >= self._new_song_cooldown_until:
            return False
        remaining = int(self._new_song_cooldown_until - now)
        mins, secs = divmod(max(0, remaining), 60)
        self.status_changed.emit(
            f"⏳ Limite para nova música atingido — retomando em {mins}m{secs:02d}s"
        )
        return True

    def _register_new_song_miss(self) -> None:
        if self._current_song_key is not None:
            return
        max_attempts = max(
            1,
            self._config.getint("Recognition", "new_song_max_attempts", fallback=12),
        )
        cooldown_s = max(
            0,
            self._config.getint("Recognition", "new_song_attempt_cooldown_s", fallback=120),
        )
        self._new_song_attempts += 1
        if self._new_song_attempts < max_attempts:
            return

        self._new_song_attempts = 0
        if cooldown_s <= 0:
            return

        self._new_song_cooldown_until = time.monotonic() + cooldown_s
        _LOG.warning(
            "Limite de tentativas para nova música atingido (%d). Pausando por %ds.",
            max_attempts,
            cooldown_s,
        )

    def _is_tracking_mode(self) -> bool:
        if not self._config.getboolean("Recognition", "continuous_tracking", fallback=True):
            return False
        return self._current_song_key is not None

    def _should_skip_recognition(self) -> bool:
        """
        Evita chamadas desnecessárias às APIs quando estamos em tracking mode
        e a música atual ainda está tocando.
        """
        if not self._is_tracking_mode():
            return False
        
        if self._current_song_duration_s <= 0:
            # Duração desconhecida, reconhecer após intervalo de tracking.
            check_interval = self._config.getint("Recognition", "tracking_interval", fallback=30)
        else:
            # Esperar até ~10s antes do fim da música para reconhecer novamente.
            check_interval = max(10, self._current_song_duration_s - 10)
        
        elapsed = time.monotonic() - self._last_recognition_time
        return elapsed < check_interval

    def _time_until_next_check(self) -> int:
        """Retorna quantos segundos faltam até a próxima verificação de reconhecimento."""
        if self._current_song_duration_s <= 0:
            check_interval = self._config.getint("Recognition", "tracking_interval", fallback=30)
        else:
            check_interval = max(10, self._current_song_duration_s - 10)
        
        elapsed = time.monotonic() - self._last_recognition_time
        return max(0, int(check_interval - elapsed))

    def _interval_for_cycle(self) -> float:
        if self._is_tracking_mode():
            return float(
                self._config.getint("Recognition", "tracking_interval", fallback=0)
            )
        return float(
            self._config.getint("Recognition", "recognition_interval", fallback=2)
        )

    def _capture_audio(self, duration: int, tracking_mode: bool):
        try:
            if tracking_mode:
                self.status_changed.emit(f"Escutando mudança de faixa ({duration}s)…")
            else:
                self.status_changed.emit(f"Capturando {duration}s de áudio…")
            capture_start = time.perf_counter()
            audio_bytes = self._audio.capture(duration)
            _LOG.debug("Captura concluída em %.1fs", time.perf_counter() - capture_start)
            return capture_start, audio_bytes
        except Exception as exc:
            _LOG.error("Erro de captura de áudio", exc_info=True)
            self.error_occurred.emit(f"Captura: {exc}")
            return None

    def _recognize_audio(self, audio_bytes: bytes, capture_start: float):
        now = time.monotonic()
        if now < self._rate_limit_until:
            remaining = int(self._rate_limit_until - now)
            mins, secs = divmod(remaining, 60)
            self.status_changed.emit(
                f"⏸ Limite da API atingido — aguardando {mins}m{secs:02d}s…"
            )
            return None

        try:
            self.status_changed.emit("Reconhecendo música…")
            t0 = time.perf_counter()
            song, cst = self._recognizer.recognize(audio_bytes, capture_start)
            _LOG.info(
                "Reconhecimento concluído em %.1fs | encontrado=%s",
                time.perf_counter() - t0,
                song is not None,
            )
            return song, cst
        except RateLimitError as exc:
            _LOG.warning("Rate limit atingido: %s", exc)
            self._rate_limit_until = time.monotonic() + self._rate_limit_cooldown_s
            self.error_occurred.emit(str(exc))
            return None
        except Exception as exc:
            _LOG.error("Erro de reconhecimento", exc_info=True)
            self.error_occurred.emit(f"Reconhecimento: {exc}")
            return None

    def _handle_song_miss(self, tracking_mode: bool) -> None:
        self._register_new_song_miss()
        self._miss_streak += 1
        if self._miss_streak >= self._miss_reset_threshold:
            self._current_song_key = None
            self._last_recognition_time = 0.0
            self._current_song_duration_s = 0.0
        self.song_not_found.emit()
        next_duration = self._capture_duration_for_cycle()
        if tracking_mode and self._current_song_key is not None:
            self.status_changed.emit(
                f"Escutando troca de faixa (sem match, próxima captura: {next_duration}s)"
            )
            return
        self.status_changed.emit(
            f"Música não reconhecida (tentativa {self._miss_streak}, próxima captura: {next_duration}s)"
        )

    def _emit_song_status(self, song, song_key: tuple[str, str, str], tracking_mode: bool) -> None:
        self.song_found.emit(song.title, song.artist, song.album)
        if tracking_mode and song_key != self._current_song_key:
            self.status_changed.emit(f"Nova faixa detectada: {song.title} — {song.artist}")
            return
        self.status_changed.emit(f"Tocando: {song.title} — {song.artist}")

    def _handle_lyrics_for_song(self, song, song_key: tuple[str, str, str], cst: float) -> None:
        """Buscar ou recuperar letras do cache para a música reconhecida."""
        # Mesma música — apenas atualizar timecode.
        if song_key == self._current_song_key:
            self.timecode_updated.emit(song.timecode_ms, cst)
            return

        self._current_song_key = song_key

        # Usar cache se disponível.
        if song_key in self._lyrics_cache:
            cached = self._lyrics_cache[song_key]
            if cached is not None:
                content, synced = cached
                self.lyrics_ready.emit(content, synced, cst)
            else:
                self.lyrics_not_found.emit()
                self.status_changed.emit(f"Letra não encontrada: {song.title}")
            self.timecode_updated.emit(song.timecode_ms, cst)
            return

        # Buscar letra pela primeira vez.
        self.lyrics_loading.emit()
        try:
            self.status_changed.emit("Buscando letra…")
            t0 = time.perf_counter()
            result = self._lyrics.fetch(song.title, song.artist, song.album, song.duration_s)
            _LOG.info("Busca de letra concluída em %.1fs | encontrada=%s", time.perf_counter() - t0, result is not None)
        except Exception as exc:
            _LOG.error("Erro ao buscar letra", exc_info=True)
            self.error_occurred.emit(f"Letra: {exc}")
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

    def _cycle(self) -> None:
        """Run one complete capture → recognise → lyrics cycle."""
        if self._new_song_limit_reached():
            return

        # Pular reconhecimento se estamos em tracking mode e a música ainda está tocando.
        if self._should_skip_recognition():
            next_check = self._time_until_next_check()
            self.status_changed.emit(f"Monitorando música atual (próxima verificação em {next_check}s)")
            return
        
        duration = self._capture_duration_for_cycle()
        tracking_mode = self._is_tracking_mode()

        captured = self._capture_audio(duration, tracking_mode)
        if captured is None:
            return
        capture_start, audio_bytes = captured

        if self._stop_flag:
            return

        recognized = self._recognize_audio(audio_bytes, capture_start)
        if recognized is None:
            return
        song, cst = recognized

        if self._stop_flag:
            return

        if song is None:
            self._handle_song_miss(tracking_mode)
            return

        self._miss_streak = 0
        self._new_song_attempts = 0
        self._new_song_cooldown_until = 0.0
        # Atualizar estado de tracking para evitar chamadas desnecessárias.
        self._last_recognition_time = time.monotonic()
        self._current_song_duration_s = song.duration_s if song.duration_s else 0.0

        song_key = (
            song.title.strip().lower(),
            song.artist.strip().lower(),
            song.album.strip().lower(),
        )

        self._save_training_audio_if_needed(song, song_key, audio_bytes)

        self._emit_song_status(song, song_key, tracking_mode)
        self._handle_lyrics_for_song(song, song_key, cst)

    def _capture_duration_for_cycle(self) -> int:
        if self._is_tracking_mode():
            base = max(
                10,
                self._config.getint("Recognition", "tracking_capture_duration", fallback=10),
            )
        else:
            base = max(10, self._config.getint("Recognition", "capture_duration", fallback=10))
        if self._miss_streak <= 0:
            return base

        # Aumenta gradualmente a janela de captura após falhas consecutivas.
        # Isso ajuda em trechos com introdução/frase curta sem exigir mudança manual.
        bonus = min(self._miss_streak, 4) * 2
        return min(12, base + bonus)
