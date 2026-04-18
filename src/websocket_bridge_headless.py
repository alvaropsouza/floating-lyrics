"""
Bridge entre o RecognitionWorkerHeadless e o WebSocket Server.

Conecta callbacks do worker (threading) aos métodos async do WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.websocket_server import get_server

_LOG = logging.getLogger(__name__)


class WebSocketBridgeHeadless:
    """
    Conecta os callbacks do RecognitionWorkerHeadless ao WebSocket Server.
    
    Como o worker roda em thread separada e o WebSocket em outro event loop,
    usamos asyncio.run_coroutine_threadsafe para comunicação thread-safe.
    """

    def __init__(self, worker, event_loop: asyncio.AbstractEventLoop, config):
        self._worker = worker
        self._loop = event_loop
        self._server = get_server()
        self._config = config
        self._setup_callbacks()

    def _setup_callbacks(self) -> None:
        """Registra todos os callbacks no worker."""
        self._worker.on('status_changed', self.on_status_changed)
        self._worker.on('song_found', self.on_song_found)
        self._worker.on('song_not_found', self.on_song_not_found)
        self._worker.on('lyrics_loading', self.on_lyrics_loading)
        self._worker.on('lyrics_ready', self.on_lyrics_ready)
        self._worker.on('lyrics_not_found', self.on_lyrics_not_found)
        self._worker.on('timecode_updated', self.on_timecode_updated)
        self._worker.on('error_occurred', self.on_error_occurred)
        self._worker.on('audio_spectrum', self.on_audio_spectrum)
        
        _LOG.info("WebSocketBridge headless configurado com sucesso")

    def _schedule_coro(self, coro) -> None:
        """Agenda coroutine no event loop asyncio de forma thread-safe."""
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            # Adicionar callback para logar erros
            future.add_done_callback(self._handle_future_result)
        else:
            _LOG.warning("Event loop não disponível ou não está rodando")
            # Fechar a corrotina para evitar warning
            coro.close()

    def _handle_future_result(self, future) -> None:
        """Loga erros de futures agendados."""
        try:
            future.result()
        except Exception as exc:
            _LOG.error(f"Erro ao executar coroutine agendada: {exc}", exc_info=True)

    # ── Callbacks do Worker ─────────────────────────────────────────────────

    def on_status_changed(self, status: str) -> None:
        """Handler para status_changed."""
        _LOG.debug(f"WS Bridge: status_changed -> {status}")
        self._schedule_coro(self._server.emit_status(status))

    def on_song_found(self, title: str, artist: str, album: str) -> None:
        """Handler para song_found."""
        _LOG.info(f"WS Bridge: song_found -> {title} - {artist}")
        self._schedule_coro(self._server.emit_song_found(title, artist, album))

    def on_song_not_found(self) -> None:
        """Handler para song_not_found."""
        _LOG.debug("WS Bridge: song_not_found")
        self._schedule_coro(self._server.emit_song_not_found())

    def on_lyrics_loading(self) -> None:
        """Handler para lyrics_loading."""
        _LOG.debug("WS Bridge: lyrics_loading")
        # Pode enviar status se quiser
        self._schedule_coro(self._server.emit_status("📝 Buscando letras..."))

    def on_lyrics_ready(self, lyrics: str, synced: bool, capture_start: float) -> None:
        """Handler para lyrics_ready."""
        _LOG.info(f"WS Bridge: lyrics_ready (synced={synced})")
        # NÃO adicionar offset manual - timecode vem compensado automaticamente
        self._schedule_coro(self._server.emit_lyrics_ready(lyrics, synced, capture_start, offset_ms=0))

    def on_lyrics_not_found(self) -> None:
        """Handler para lyrics_not_found."""
        _LOG.debug("WS Bridge: lyrics_not_found")
        self._schedule_coro(self._server.emit_lyrics_not_found())

    def on_timecode_updated(self, timecode_ms: int, capture_start: float) -> None:
        """Handler para timecode_updated."""
        # NÃO adicionar offset manual - o timecode já vem compensado automaticamente
        self._schedule_coro(self._server.emit_timecode_update(timecode_ms, capture_start, offset_ms=0))

    def on_error_occurred(self, error: str) -> None:
        """Handler para error_occurred."""
        _LOG.warning(f"WS Bridge: error -> {error}")
        self._schedule_coro(self._server.emit_error(error))

    def on_audio_spectrum(self, spectrum: list[float]) -> None:
        """Handler para audio_spectrum."""
        self._schedule_coro(self._server.emit_audio_spectrum(spectrum))
