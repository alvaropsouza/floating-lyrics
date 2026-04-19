"""
Qt adapter for the canonical recognition worker implementation.

The headless worker in ``src.worker_headless`` now owns the capture ->
recognise -> lyrics pipeline. This module only exposes a Qt-friendly facade
with signals and ``start/stop/wait/isRunning`` methods so the PyQt UI can keep
its existing integration points without maintaining a second worker
implementation.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from src.worker_headless import RecognitionWorkerHeadless


class RecognitionWorker(QObject):
    """Qt facade over ``RecognitionWorkerHeadless``."""

    status_changed = pyqtSignal(str)
    song_found = pyqtSignal(str, str, str)
    song_not_found = pyqtSignal()
    lyrics_loading = pyqtSignal()
    lyrics_ready = pyqtSignal(str, bool, float)
    lyrics_not_found = pyqtSignal()
    timecode_updated = pyqtSignal(int, float)
    error_occurred = pyqtSignal(str)

    def __init__(self, config, audio_capture, recognizer, lyrics_fetcher, parent=None):
        super().__init__(parent)
        self._config = config
        self._audio = audio_capture
        self._recognizer = recognizer
        self._lyrics_fetcher = lyrics_fetcher
        self._worker: RecognitionWorkerHeadless | None = None
        self._create_worker()

    @property
    def recognizer(self):
        return self._recognizer

    def _create_worker(self) -> None:
        worker = RecognitionWorkerHeadless(
            self._config,
            self._audio,
            self._recognizer,
            self._lyrics_fetcher,
        )
        worker.on("status_changed", self.status_changed.emit)
        worker.on("song_found", self.song_found.emit)
        worker.on("song_not_found", self.song_not_found.emit)
        worker.on("lyrics_loading", self.lyrics_loading.emit)
        worker.on("lyrics_ready", self.lyrics_ready.emit)
        worker.on("lyrics_not_found", self.lyrics_not_found.emit)
        worker.on("timecode_updated", self.timecode_updated.emit)
        worker.on("error_occurred", self.error_occurred.emit)
        self._worker = worker

    def start(self) -> None:
        if self.isRunning():
            return
        # Python threads cannot be started twice; recreate after a full stop.
        if self._worker is None or self._worker.ident is not None:
            self._create_worker()
        self._worker.start()

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()

    def wait(self, timeout: int | None = None) -> None:
        if self._worker is None:
            return
        timeout_s = None if timeout is None else timeout / 1000.0
        self._worker.join(timeout_s)

    def isRunning(self) -> bool:
        return self._worker is not None and self._worker.is_alive()
