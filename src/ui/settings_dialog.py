"""
Settings dialog — opened by the ⚙ button in LyricsWindow.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

_STYLE = """
QDialog, QWidget {
    background-color: #12122A;
    color: #DDDDEE;
    font-family: "Segoe UI";
    font-size: 13px;
}
QPushButton {
    background-color: #1E2050;
    color: #DDDDEE;
    border: 1px solid #3A3A6A;
    border-radius: 6px;
    padding: 6px 16px;
}
QPushButton:hover  { background-color: #2A2F6A; }
QPushButton:pressed { background-color: #4433AA; }
QGroupBox {
    border: 1px solid #2A2A4A;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 4px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    color: #8888CC;
    font-weight: bold;
}
QLabel   { color: #BBBBCC; }
QLineEdit {
    background: #0E0E22;
    border: 1px solid #3A3A6A;
    border-radius: 4px;
    padding: 4px 8px;
    color: #DDDDEE;
}
QLineEdit:focus { border-color: #6677DD; }
QSlider::groove:horizontal {
    height: 4px; background: #2A2A4A; border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 14px; height: 14px;
    background: #5566DD; border-radius: 7px; margin: -5px 0;
}
QSlider::sub-page:horizontal { background: #5566DD; border-radius: 2px; }
QCheckBox           { color: #BBBBCC; }
QCheckBox::indicator:checked { background: #5566DD; border-radius: 3px; }
QSpinBox {
    background: #0E0E22;
    border: 1px solid #3A3A6A;
    border-radius: 4px;
    padding: 3px 6px;
    color: #DDDDEE;
}
"""


class SettingsDialog(QDialog):
    """Non-modal settings panel opened by the gear button."""

    def __init__(self, config, lyrics_window, worker, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._lw     = lyrics_window
        self._worker = worker

        self.setWindowTitle("Configurações — Floating Lyrics")
        self.setMinimumWidth(420)
        self.setMaximumWidth(520)
        self.setModal(False)

        self._build_ui()
        self._connect()
        self.setStyleSheet(_STYLE)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addWidget(self._build_api_group())
        layout.addWidget(self._build_display_group())
        layout.addStretch()

    def _build_api_group(self) -> QGroupBox:
        grp = QGroupBox("Chaves de API")
        fl  = QFormLayout(grp)

        self._edit_audd = QLineEdit()
        self._edit_audd.setPlaceholderText("Chave AudD (vazio = modo de teste, ~10 req/dia)")
        self._edit_audd.setEchoMode(QLineEdit.EchoMode.Password)
        self._edit_audd.setText(self._config.get("API", "audd_api_key", fallback=""))

        lnk_audd = QLabel(
            '<a href="https://dashboard.audd.io/" style="color:#8899FF;">'
            "Obter chave gratuita AudD →</a>"
        )
        lnk_audd.setOpenExternalLinks(True)

        self._edit_mx = QLineEdit()
        self._edit_mx.setPlaceholderText("Musixmatch (opcional — fallback de letras simples)")
        self._edit_mx.setEchoMode(QLineEdit.EchoMode.Password)
        self._edit_mx.setText(self._config.get("API", "musixmatch_api_key", fallback=""))

        lnk_mx = QLabel(
            '<a href="https://developer.musixmatch.com/" style="color:#8899FF;">'
            "Obter chave gratuita Musixmatch →</a>"
        )
        lnk_mx.setOpenExternalLinks(True)

        self._btn_save_api = QPushButton("Salvar chaves de API")

        fl.addRow("AudD API Key:",   self._edit_audd)
        fl.addRow("",                lnk_audd)
        fl.addRow("Musixmatch Key:", self._edit_mx)
        fl.addRow("",                lnk_mx)
        fl.addRow("",                self._btn_save_api)
        return grp

    def _build_display_group(self) -> QGroupBox:
        grp = QGroupBox("Exibição")
        fl  = QFormLayout(grp)

        # Opacity
        self._slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self._slider_opacity.setRange(10, 100)
        init_op = int(self._config.getfloat("Display", "opacity", fallback=0.95) * 100)
        self._slider_opacity.setValue(init_op)
        self._lbl_opacity = QLabel(f"{init_op}%")
        self._lbl_opacity.setFixedWidth(36)
        op_row = QHBoxLayout()
        op_row.addWidget(self._slider_opacity)
        op_row.addWidget(self._lbl_opacity)
        fl.addRow("Opacidade:", op_row)

        # Font size
        self._spin_font = QSpinBox()
        self._spin_font.setRange(8, 48)
        self._spin_font.setValue(self._config.getint("Display", "font_size", fallback=16))
        self._spin_font.setSuffix(" pt")
        fl.addRow("Tamanho da fonte:", self._spin_font)

        # LRC sync offset
        self._spin_offset = QSpinBox()
        self._spin_offset.setRange(-5000, 5000)
        self._spin_offset.setValue(self._config.getint("Display", "lrc_offset_ms", fallback=0))
        self._spin_offset.setSuffix(" ms")
        self._spin_offset.setToolTip(
            "Ajuste fino de sincronização.\n"
            "Positivo → letra aparece mais tarde.\n"
            "Negativo → letra aparece mais cedo."
        )
        fl.addRow("Offset de sync:", self._spin_offset)

        # Always on top
        self._chk_aot = QCheckBox("Sempre visível sobre outras janelas")
        self._chk_aot.setChecked(
            self._config.getboolean("Display", "always_on_top", fallback=True)
        )
        fl.addRow("", self._chk_aot)

        self._btn_apply = QPushButton("Aplicar")
        fl.addRow("", self._btn_apply)
        return grp

    # ── Signals ───────────────────────────────────────────────────────────────

    def _connect(self) -> None:
        self._btn_save_api.clicked.connect(self._save_api)
        self._btn_apply.clicked.connect(self._save_display)
        self._slider_opacity.valueChanged.connect(self._live_opacity)
        self._chk_aot.toggled.connect(self._live_aot)

    @pyqtSlot()
    def _save_api(self) -> None:
        self._config.set("API", "audd_api_key",       self._edit_audd.text().strip())
        self._config.set("API", "musixmatch_api_key", self._edit_mx.text().strip())
        self._config.save()
        self._worker.recognizer.api_key = self._edit_audd.text().strip()
        self._btn_save_api.setText("Salvo ✓")
        QTimer.singleShot(2000, lambda: self._btn_save_api.setText("Salvar chaves de API"))

    @pyqtSlot()
    def _save_display(self) -> None:
        self._config.set("Display", "font_size",     self._spin_font.value())
        self._config.set("Display", "lrc_offset_ms", self._spin_offset.value())
        self._config.save()
        self._lw.refresh_display()
        self._btn_apply.setText("Aplicado ✓")
        QTimer.singleShot(2000, lambda: self._btn_apply.setText("Aplicar"))

    @pyqtSlot(int)
    def _live_opacity(self, value: int) -> None:
        self._lbl_opacity.setText(f"{value}%")
        opacity = value / 100.0
        self._lw.set_opacity(opacity)
        self._config.set("Display", "opacity", opacity)
        self._config.save()

    @pyqtSlot(bool)
    def _live_aot(self, checked: bool) -> None:
        self._lw.set_always_on_top(checked)
        self._config.set("Display", "always_on_top", checked)
        self._config.save()
