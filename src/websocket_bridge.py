"""
Bridge entre o RecognitionWorker (Qt) e o WebSocket Server (asyncio).

Permite que o frontend Flutter receba eventos em tempo real do worker Python.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PyQt6.QtCore import QObject, pyqtSlot

from src.websocket_server import get_server

_LOG = logging.getLogger(__name__)


class WebSocketBridge(QObject):
    """
    Conecta os sinais do RecognitionWorker ao WebSocket Server.
    
    Como Qt e asyncio rodam em threads diferentes, usamos QMetaObject.invokeMethod
    para enviar eventos do worker Qt para o loop asyncio.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server_task: asyncio.Task | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Define o event loop asyncio para enviar eventos."""
        self._loop = loop
        _LOG.info(f"Event loop configurado no WebSocketBridge: {loop}")

    def _schedule_coro(self, coro) -> None:
        """Agenda coroutine no event loop asyncio de forma thread-safe."""
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            # Adicionar callback para logar erros
            future.add_done_callback(self._handle_future_result)
        else:
            _LOG.warning(f"Event loop não disponível ou não está rodando. Loop: {self._loop}")
            # Fechar a corrotina para evitar warning
            coro.close()

    def _handle_future_result(self, future) -> None:
        """Loga erros de futures agendados."""
        try:
            future.result()
        except Exception as exc:
            _LOG.error(f"Erro ao executar coroutine agendada: {exc}", exc_info=True)

    # ── Slots conectados aos sinais do Worker ──────────────────────────────

    @pyqtSlot(str)
    def on_status_changed(self, status: str) -> None:
        """Handler para status_changed signal."""
        _LOG.debug(f"WS Bridge: status_changed -> {status}")
        server = get_server()
        self._schedule_coro(server.emit_status(status))

    @pyqtSlot(str, str, str)
    def on_song_found(self, title: str, artist: str, album: str) -> None:
        """Handler para song_found signal."""
        _LOG.info(f"WS Bridge: song_found -> {title} - {artist}")
        server = get_server()
        self._schedule_coro(server.emit_song_found(title, artist, album))

    @pyqtSlot()
    def on_song_not_found(self) -> None:
        """Handler para song_not_found signal."""
        _LOG.debug("WS Bridge: song_not_found")
        server = get_server()
        self._schedule_coro(server.emit_song_not_found())

    @pyqtSlot(str, bool, float)
    def on_lyrics_ready(self, lyrics: str, synced: bool, capture_start: float) -> None:
        """Handler para lyrics_ready signal."""
        _LOG.info(f"WS Bridge: lyrics_ready (synced={synced})")
        server = get_server()
        self._schedule_coro(server.emit_lyrics_ready(lyrics, synced))

    @pyqtSlot()
    def on_lyrics_not_found(self) -> None:
        """Handler para lyrics_not_found signal."""
        _LOG.debug("WS Bridge: lyrics_not_found")
        server = get_server()
        self._schedule_coro(server.emit_lyrics_not_found())

    @pyqtSlot(int, float)
    def on_timecode_updated(self, timecode_ms: int, capture_start: float) -> None:
        """Handler para timecode_updated signal."""
        server = get_server()
        self._schedule_coro(server.emit_timecode_update(timecode_ms))

    @pyqtSlot(str)
    def on_error_occurred(self, error: str) -> None:
        """Handler para error_occurred signal."""
        _LOG.warning(f"WS Bridge: error -> {error}")
        server = get_server()
        self._schedule_coro(server.emit_error(error))
