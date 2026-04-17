"""
Floating Lyrics — Entry point.

Initialises all components, creates the Qt application and launches
the main control window together with the floating lyrics overlay.
"""

import logging
import sys
from pathlib import Path

# Make sure the project root is on sys.path so `src.*` imports resolve.
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QFont, QPixmap, QPainter, QColor, QBrush, QIcon
from PyQt6.QtCore import Qt

from config import Config
from src.audio_capture import AudioCapture, AudioCaptureError
from src.song_recognition import AudDRecognizer
from src.lyrics_fetcher import LyricsFetcher
from src.worker import RecognitionWorker
from src.ui.main_window import MainWindow


def _build_tray_icon() -> QIcon:
    """Create a simple music-note icon programmatically (no file needed)."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor("#5566CC")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, 30, 30)
    painter.setPen(QColor("white"))
    f = QFont("Segoe UI", 16, QFont.Weight.Bold)
    painter.setFont(f)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "♪")
    painter.end()
    return QIcon(pixmap)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Floating Lyrics")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("FloatingLyrics")
    # Keep the app alive even when all windows are hidden (tray mode).
    app.setQuitOnLastWindowClosed(False)

    icon = _build_tray_icon()
    app.setWindowIcon(icon)

    config = Config()

    # ── Audio capture ──────────────────────────────────────────────────────
    audio_capture = AudioCapture(config)
    try:
        audio_capture.initialize()
    except AudioCaptureError as exc:
        QMessageBox.critical(
            None,
            "Erro de inicialização de áudio",
            f"{exc}\n\n"
            "Verifique se o pyaudiowpatch está instalado e se há um\n"
            "dispositivo de saída de áudio ativo no Windows.",
        )
        return 1

    # ── Sub-components ─────────────────────────────────────────────────────
    recognizer = AudDRecognizer(config.get("API", "audd_api_key"))
    lyrics_fetcher = LyricsFetcher(config)

    worker = RecognitionWorker(config, audio_capture, recognizer, lyrics_fetcher)

    # ── UI ─────────────────────────────────────────────────────────────────
    window = MainWindow(config, worker, icon)
    window.show()

    exit_code = app.exec()

    # ── Graceful shutdown ──────────────────────────────────────────────────
    if worker.isRunning():
        worker.stop()
        worker.wait(4000)
    audio_capture.cleanup()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
