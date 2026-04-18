"""
Servidor WebSocket para comunicação com o frontend Flutter.

Transmite eventos de reconhecimento em tempo real:
- Status de captura
- Músicas reconhecidas
- Letras sincronizadas
- Atualizações de timecode
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Set

from aiohttp import web
from aiohttp import WSMsgType

_LOG = logging.getLogger(__name__)


class WebSocketServer:
    """Servidor WebSocket para broadcast de eventos de reconhecimento."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self._app = web.Application()
        self._clients: Set[web.WebSocketResponse] = set()
        self._runner: web.AppRunner | None = None
        
        # Registrar rotas
        self._app.router.add_get('/ws', self._websocket_handler)
        self._app.router.add_get('/health', self._health_check)

    async def start(self) -> None:
        """Inicia o servidor WebSocket."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        _LOG.info(f"WebSocket server rodando em ws://{self.host}:{self.port}/ws")

    async def stop(self) -> None:
        """Para o servidor e fecha todas as conexões."""
        # Fechar todos os clientes
        for ws in list(self._clients):
            await ws.close()
        self._clients.clear()
        
        # Parar o runner
        if self._runner:
            await self._runner.cleanup()
        _LOG.info("WebSocket server parado")

    async def _health_check(self, request: web.Request) -> web.Response:
        """Endpoint de health check."""
        return web.Response(text=json.dumps({
            "status": "ok",
            "clients": len(self._clients)
        }), content_type="application/json")

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handler para conexões WebSocket."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        self._clients.add(ws)
        _LOG.info(f"Cliente conectado. Total: {len(self._clients)}")
        
        # Enviar mensagem de boas-vindas
        await self._send_to_client(ws, {
            "type": "connected",
            "data": {"message": "Conectado ao Floating Lyrics"}
        })
        
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_message(ws, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    _LOG.error(f"WebSocket error: {ws.exception()}")
        finally:
            self._clients.discard(ws)
            _LOG.info(f"Cliente desconectado. Total: {len(self._clients)}")
        
        return ws

    async def _handle_message(self, ws: web.WebSocketResponse, data: str) -> None:
        """Processa mensagens recebidas do cliente."""
        try:
            msg = json.loads(data)
            msg_type = msg.get("type")
            
            _LOG.debug(f"Mensagem recebida: {msg_type}")
            
            # Aqui você pode adicionar handlers para comandos do Flutter
            # Por exemplo: {"type": "set_config", "data": {...}}
            if msg_type == "ping":
                await self._send_to_client(ws, {"type": "pong", "data": {}})
            elif msg_type == "get_status":
                # Enviar status atual (pode integrar com o worker)
                await self._send_to_client(ws, {
                    "type": "status",
                    "data": {"message": "Sistema pronto"}
                })
                
        except json.JSONDecodeError:
            _LOG.warning(f"Mensagem inválida recebida: {data}")
        except Exception as exc:
            _LOG.error(f"Erro ao processar mensagem: {exc}", exc_info=True)

    async def _send_to_client(self, ws: web.WebSocketResponse, message: dict) -> None:
        """Envia mensagem para um cliente específico."""
        try:
            await ws.send_json(message)
        except Exception as exc:
            _LOG.error(f"Erro ao enviar mensagem: {exc}")

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Envia mensagem para todos os clientes conectados."""
        if not self._clients:
            return
        
        disconnected = set()
        
        for ws in self._clients:
            try:
                await ws.send_json(message)
            except Exception as exc:
                _LOG.error(f"Erro ao enviar broadcast: {exc}")
                disconnected.add(ws)
        
        # Remover clientes desconectados
        self._clients -= disconnected

    # ── Métodos de conveniência para eventos comuns ─────────────────────────

    async def emit_status(self, status: str) -> None:
        """Emite atualização de status."""
        await self.broadcast({
            "type": "status_changed",
            "data": {"status": status}
        })

    async def emit_song_found(self, title: str, artist: str, album: str) -> None:
        """Emite evento de música encontrada."""
        await self.broadcast({
            "type": "song_found",
            "data": {
                "title": title,
                "artist": artist,
                "album": album
            }
        })

    async def emit_song_not_found(self) -> None:
        """Emite evento de música não encontrada."""
        await self.broadcast({
            "type": "song_not_found",
            "data": {}
        })

    async def emit_lyrics_ready(self, lyrics: str, synced: bool) -> None:
        """Emite evento de letras prontas."""
        await self.broadcast({
            "type": "lyrics_ready",
            "data": {
                "lyrics": lyrics,
                "synced": synced
            }
        })

    async def emit_lyrics_not_found(self) -> None:
        """Emite evento de letras não encontradas."""
        await self.broadcast({
            "type": "lyrics_not_found",
            "data": {}
        })

    async def emit_timecode_update(self, timecode_ms: int) -> None:
        """Emite atualização de timecode para sincronização."""
        await self.broadcast({
            "type": "timecode_updated",
            "data": {"timecode_ms": timecode_ms}
        })

    async def emit_error(self, error: str) -> None:
        """Emite evento de erro."""
        await self.broadcast({
            "type": "error",
            "data": {"message": error}
        })

    async def emit_audio_spectrum(self, spectrum: list[float]) -> None:
        """Emite dados de espectro de áudio para visualização."""
        await self.broadcast({
            "type": "audio_spectrum",
            "data": {"spectrum": spectrum}
        })


# ── Singleton global ────────────────────────────────────────────────────────

_server_instance: WebSocketServer | None = None


def get_server() -> WebSocketServer:
    """Retorna a instância singleton do servidor."""
    global _server_instance
    if _server_instance is None:
        _server_instance = WebSocketServer()
    return _server_instance
