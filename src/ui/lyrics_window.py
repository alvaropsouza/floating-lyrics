"""
Lyrics window — the primary UI window for Floating Lyrics.

Layout
──────
┌───────────────────────────────────────────────────────────┐
│  ♪  Song — Artist                           [⚙]  [■]  [×] │ ← title bar (drag)
├───────────────────────────────────────────────────────────┤
│                                                           │
│          context line  (dimmed)                           │
│        ████  CURRENT LINE  (bold, white)  ████            │ ← auto-scrolls
│          context line  (dimmed)                           │
│                                                           │
├───────────────────────────────────────────────────────────┤
│  ○ Capturando 10s…                          [resize grip] │ ← status bar
└───────────────────────────────────────────────────────────┘

• Frameless, draggable from the title bar.
• Proper QScrollArea with one QLabel per lyric line — no more stuck lyrics.
• Current line auto-scrolls to the vertical center of the viewport.
• ⚙ opens SettingsDialog.  ■/▶ toggles the worker.  × hides to tray.
• always-on-top and opacity configurable via Settings.
"""

from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from src.lyrics_parser import LrcLine, find_current_line, parse_lrc

_BAR_H    = 36   # title bar height (px)
_STATUS_H = 28   # status bar height (px)

_STYLE = """
/* ── Window background ──────────────────────────────── */
LyricsWindow {
    background-color: #1A1A2E;
}

/* ── Title bar ───────────────────────────────────────── */
QWidget#titleBar {
    background-color: #12122A;
    border-bottom: 1px solid #252545;
}
QLabel#songLabel {
    color: #8888CC;
    font: bold 10pt 'Segoe UI';
    background: transparent;
    padding-left: 4px;
}

/* ── Title-bar icon buttons ──────────────────────────── */
QPushButton#barBtn, QPushButton#barBtnClose {
    background: transparent;
    border: none;
    border-radius: 5px;
    padding: 0;
    min-width:  26px;  max-width:  26px;
    min-height: 26px;  max-height: 26px;
}
QPushButton#barBtn       { color: #6666AA; font-size: 14px; }
QPushButton#barBtnClose  { color: #773333; font-size: 16px; }
QPushButton#barBtn:hover      { background: #252545; color: #CCCCEE; }
QPushButton#barBtnClose:hover { background: #3A1A1A; color: #FFAAAA; }

/* ── Scroll area ─────────────────────────────────────── */
QScrollArea, QWidget#lyricsInner {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: #12122A;
    width: 5px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3A3A6A;
    border-radius: 2px;
    min-height: 24px;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical  { height: 0; }
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical  { background: none; }

/* ── Status bar ──────────────────────────────────────── */
QWidget#statusBar {
    background-color: #0E0E22;
    border-top: 1px solid #252545;
}
QLabel#statusLabel {
    color: #4A4A6A;
    font: 9pt 'Segoe UI';
    background: transparent;
    padding: 0 10px;
}
QSizeGrip { background: transparent; width: 14px; height: 14px; }
"""


class LyricsWindow(QWidget):
    """Primary frameless window — shows real-time synchronised lyrics."""

    def __init__(self, config, worker, icon: QIcon, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        self._worker = worker
        self._icon   = icon

        # ── LRC state ────────────────────────────────────────────────────────
        self._lrc_lines:    list[LrcLine] = []
        self._plain_lines:  list[str]     = []
        self._is_synced:    bool          = False
        self._timecode_ms:  int           = 0
        self._capture_start: float        = 0.0
        self._current_idx:  int           = -1

        # ── Display cache (rebuilt from config when settings change) ─────────
        self._font_size:     int = 16
        self._font_color:    str = "#FFFFFF"
        self._lrc_offset_ms: int = 0

        # ── UI helpers ────────────────────────────────────────────────────────
        self._drag_pos: Optional[QPoint] = None
        self._line_labels: list[QLabel]  = []
        self._settings_dlg               = None   # lazy SettingsDialog

        # ── Sync timer (single-shot, scheduled per-line) ─────────────────────
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._tick)

        # ── Build everything ──────────────────────────────────────────────────
        self._setup_window()
        self._build_ui()
        self._connect_worker()
        self._setup_tray()
        self._apply_config()

        # Auto-start after event loop is running
        QTimer.singleShot(200, self._start_worker)

    # ── Window flags ─────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(380, 220)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet(_STYLE)
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._build_title_bar())
        root.addWidget(self._build_scroll_area(), stretch=1)
        root.addWidget(self._build_status_bar())

    def _build_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(_BAR_H)

        hl = QHBoxLayout(bar)
        hl.setContentsMargins(12, 0, 8, 0)
        hl.setSpacing(4)

        self._lbl_song = QLabel("♪  Floating Lyrics")
        self._lbl_song.setObjectName("songLabel")
        self._lbl_song.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._lbl_song.setMinimumWidth(0)
        hl.addWidget(self._lbl_song)

        self._btn_cfg = QPushButton("⚙")
        self._btn_cfg.setObjectName("barBtn")
        self._btn_cfg.setToolTip("Configurações")
        self._btn_cfg.clicked.connect(self._open_settings)

        self._btn_toggle = QPushButton("■")
        self._btn_toggle.setObjectName("barBtn")
        self._btn_toggle.setToolTip("Parar reconhecimento")
        self._btn_toggle.clicked.connect(self._toggle_worker)

        btn_close = QPushButton("×")
        btn_close.setObjectName("barBtnClose")
        btn_close.setToolTip("Minimizar para a bandeja do sistema")
        btn_close.clicked.connect(self._hide_to_tray)

        hl.addWidget(self._btn_cfg)
        hl.addWidget(self._btn_toggle)
        hl.addWidget(btn_close)
        return bar

    def _build_scroll_area(self) -> QScrollArea:
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._lyrics_inner = QWidget()
        self._lyrics_inner.setObjectName("lyricsInner")

        self._lyrics_layout = QVBoxLayout(self._lyrics_inner)
        self._lyrics_layout.setSpacing(2)
        self._lyrics_layout.setContentsMargins(18, 16, 18, 16)
        self._lyrics_layout.addStretch()

        self._scroll.setWidget(self._lyrics_inner)
        return self._scroll

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("statusBar")
        bar.setFixedHeight(_STATUS_H)

        hl = QHBoxLayout(bar)
        hl.setContentsMargins(0, 0, 4, 0)
        hl.setSpacing(0)

        self._lbl_status = QLabel("Iniciando…")
        self._lbl_status.setObjectName("statusLabel")
        hl.addWidget(self._lbl_status)

        grip = QSizeGrip(self)
        hl.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        return bar

    # ── Config ────────────────────────────────────────────────────────────────

    def _apply_config(self) -> None:
        x = self._config.getint("Display", "window_x",      fallback=200)
        y = self._config.getint("Display", "window_y",      fallback=200)
        w = self._config.getint("Display", "window_width",  fallback=600)
        h = self._config.getint("Display", "window_height", fallback=400)
        self.setGeometry(x, y, w, h)

        opacity = self._config.getfloat("Display", "opacity", fallback=0.95)
        self.setWindowOpacity(max(0.1, min(1.0, opacity)))

        aot = self._config.getboolean("Display", "always_on_top", fallback=True)
        self._set_always_on_top(aot)
        self._refresh_cache()

    def _refresh_cache(self) -> None:
        """Snapshot display values — called after settings change."""
        self._font_size     = self._config.getint("Display", "font_size",     fallback=16)
        self._font_color    = self._config.get("Display",    "font_color",    fallback="#FFFFFF")
        self._lrc_offset_ms = self._config.getint("Display", "lrc_offset_ms", fallback=0)

    def _set_always_on_top(self, enabled: bool) -> None:
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    # ── Public API (for SettingsDialog) ───────────────────────────────────────

    def set_always_on_top(self, enabled: bool) -> None:
        self._set_always_on_top(enabled)

    def set_opacity(self, value: float) -> None:
        self.setWindowOpacity(max(0.1, min(1.0, value)))

    def refresh_display(self) -> None:
        """Reload cache and re-style labels after a display settings change."""
        self._refresh_cache()
        self._update_line_styles(self._current_idx)

    # ── System tray ───────────────────────────────────────────────────────────

    def _setup_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._icon, self)
        self._tray.setToolTip("Floating Lyrics")

        menu = QMenu()
        act_show = QAction("Mostrar", self)
        act_show.triggered.connect(self._restore)
        act_quit = QAction("Sair", self)
        act_quit.triggered.connect(QApplication.quit)
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _restore(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _hide_to_tray(self) -> None:
        self.hide()
        self._tray.showMessage(
            "Floating Lyrics",
            "Minimizado para a bandeja. Clique duplo para restaurar.",
            QSystemTrayIcon.MessageType.Information,
            2000,
        )

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore()

    # ── Worker control ────────────────────────────────────────────────────────

    def _start_worker(self) -> None:
        key = self._config.get("API", "audd_api_key", fallback="")
        self._worker.recognizer.api_key = key
        if not self._worker.isRunning():
            self._worker.start()
        self._btn_toggle.setText("■")
        self._btn_toggle.setToolTip("Parar reconhecimento")

    def _toggle_worker(self) -> None:
        if self._worker.isRunning():
            self._worker.stop()
            self._btn_toggle.setText("▶")
            self._btn_toggle.setToolTip("Iniciar reconhecimento")
            self._lbl_status.setText("Parado")
        else:
            self._start_worker()

    def _open_settings(self) -> None:
        from src.ui.settings_dialog import SettingsDialog
        if self._settings_dlg is None or not self._settings_dlg.isVisible():
            self._settings_dlg = SettingsDialog(
                self._config, self, self._worker, parent=self
            )
        self._settings_dlg.show()
        self._settings_dlg.activateWindow()
        self._settings_dlg.raise_()

    # ── Worker signal connections ─────────────────────────────────────────────

    def _connect_worker(self) -> None:
        self._worker.status_changed.connect(self._on_status)
        self._worker.song_found.connect(self._on_song_found)
        self._worker.song_not_found.connect(self._on_song_not_found)
        self._worker.lyrics_ready.connect(self._on_lyrics_ready)
        self._worker.lyrics_not_found.connect(self._on_lyrics_not_found)
        self._worker.timecode_updated.connect(self._on_timecode_updated)
        self._worker.error_occurred.connect(self._on_error)

    @pyqtSlot(str)
    def _on_status(self, msg: str) -> None:
        self._lbl_status.setText(msg)

    @pyqtSlot(str, str, str)
    def _on_song_found(self, title: str, artist: str, _album: str) -> None:
        text = f"♪  {title}  —  {artist}" if artist else f"♪  {title}"
        self._lbl_song.setText(text)

    @pyqtSlot()
    def _on_song_not_found(self) -> None:
        pass  # status_changed carries the user-visible message

    @pyqtSlot(str, bool, float)
    def _on_lyrics_ready(self, content: str, synced: bool, capture_start: float) -> None:
        self._is_synced     = synced
        self._capture_start = capture_start
        self._current_idx   = -1
        self._sync_timer.stop()
        self._refresh_cache()

        if synced:
            self._lrc_lines, _ = parse_lrc(content)
            self._plain_lines  = []
            lines = [ln.text for ln in self._lrc_lines]
        else:
            self._lrc_lines   = []
            self._plain_lines = [l for l in content.splitlines() if l.strip()]
            lines = self._plain_lines

        self._load_labels(lines)

        if synced:
            self._schedule_next_tick()

    @pyqtSlot()
    def _on_lyrics_not_found(self) -> None:
        pass  # status_changed carries the message

    @pyqtSlot(int, float)
    def _on_timecode_updated(self, timecode_ms: int, capture_start: float) -> None:
        self._timecode_ms   = timecode_ms
        self._capture_start = capture_start
        if self._is_synced and self._lrc_lines:
            self._tick()

    @pyqtSlot(str)
    def _on_error(self, msg: str) -> None:
        self._lbl_status.setText(f"⚠  {msg}")

    # ── Lyrics label management ───────────────────────────────────────────────

    def _load_labels(self, lines: list[str]) -> None:
        """Rebuild the scrollable label list for a new lyrics set."""
        while self._lyrics_layout.count():
            item = self._lyrics_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._line_labels.clear()

        font_size = self._font_size
        for text in lines:
            lbl = QLabel(text or " ")
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            lbl.setStyleSheet(
                f"color: rgba(255,255,255,55); "
                f"font: {font_size}pt 'Segoe UI'; "
                "background: transparent; padding: 2px 0;"
            )
            self._lyrics_layout.addWidget(lbl)
            self._line_labels.append(lbl)

        self._lyrics_layout.addStretch()
        self._update_line_styles(-1)

    def _update_line_styles(self, current_idx: int) -> None:
        """Re-style every label — highlight 3 lines (prev, current, next)."""
        if not self._line_labels:
            return

        size  = self._font_size
        color = QColor(self._font_color)
        r, g, b = color.red(), color.green(), color.blue()

        highlight = {current_idx - 1, current_idx, current_idx + 1}

        for i, lbl in enumerate(self._line_labels):
            if i == current_idx:
                # Centre line — boldest
                lbl.setStyleSheet(
                    f"color: rgb({r},{g},{b}); "
                    f"font: bold {size + 2}pt 'Segoe UI'; "
                    "background: transparent; padding: 4px 0;"
                )
            elif i in highlight:
                # Adjacent lines — bright but slightly smaller
                lbl.setStyleSheet(
                    f"color: rgba({r},{g},{b},210); "
                    f"font: {size}pt 'Segoe UI'; "
                    "background: transparent; padding: 3px 0;"
                )
            else:
                dist  = abs(i - current_idx) if current_idx >= 0 else 99
                alpha = max(40, 210 - dist * 52)
                lbl.setStyleSheet(
                    f"color: rgba({r},{g},{b},{alpha}); "
                    f"font: {size}pt 'Segoe UI'; "
                    "background: transparent; padding: 2px 0;"
                )

        if 0 <= current_idx < len(self._line_labels):
            self._scroll_to_label(self._line_labels[current_idx])

    def _scroll_to_label(self, lbl: QLabel) -> None:
        """Scroll so the label is vertically centred inside the viewport."""
        vp_h   = self._scroll.viewport().height()
        lbl_y  = lbl.pos().y()
        target = lbl_y + lbl.height() // 2 - vp_h // 2
        self._scroll.verticalScrollBar().setValue(max(0, target))

    # ── LRC sync ─────────────────────────────────────────────────────────────

    def _elapsed_ms(self) -> int:
        delta = (time.perf_counter() - self._capture_start) * 1000
        return int(self._timecode_ms + delta + self._lrc_offset_ms)

    def _tick(self) -> None:
        """Advance the highlighted line; called by the single-shot timer."""
        if not self._is_synced or not self._lrc_lines:
            return
        new_idx = find_current_line(self._lrc_lines, self._elapsed_ms())
        if new_idx != self._current_idx:
            self._current_idx = new_idx
            self._update_line_styles(new_idx)
        self._schedule_next_tick()

    def _schedule_next_tick(self) -> None:
        """Schedule the timer to fire exactly when the next LRC line is due."""
        self._sync_timer.stop()
        if not self._is_synced or not self._lrc_lines:
            return
        elapsed  = self._elapsed_ms()
        next_idx = find_current_line(self._lrc_lines, elapsed) + 1
        if next_idx >= len(self._lrc_lines):
            return
        delay = max(5, self._lrc_lines[next_idx].time_ms - elapsed - 20)
        self._sync_timer.start(delay)

    # ── Mouse events (title-bar drag) ─────────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if event.position().y() <= _BAR_H:
                self._drag_pos = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_pos is not None:
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)
            self._config.set("Display", "window_x", new_pos.x())
            self._config.set("Display", "window_y", new_pos.y())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_pos = None
        self._config.save()

    # ── Window events ─────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._config.set("Display", "window_width",  self.width())
        self._config.set("Display", "window_height", self.height())

    def closeEvent(self, event) -> None:  # noqa: N802
        self._config.save()
        event.accept()

