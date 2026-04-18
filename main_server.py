"""
Modo servidor: Roda o backend Python sem UI, apenas WebSocket.

Ideal para usar com o frontend Flutter. O Flutter se conecta via WebSocket
e recebe todos os eventos de reconhecimento em tempo real.

Uso:
    python main_server.py
"""

import asyncio
import logging
import signal
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / "server.log", encoding="utf-8")
    ]
)

_LOG = logging.getLogger(__name__)

from config import Config
from src.audio_capture import AudioCapture
from src.lyrics_fetcher import LyricsFetcher
from src.song_recognition import AudDRecognizer, ACRCloudRecognizer, MultiProviderRecognizer
from src.websocket_bridge import WebSocketBridge
from src.websocket_server import get_server
from src.worker import RecognitionWorker


class BackendServer:
    """Servidor backend que combina Qt Worker + WebSocket Server."""

    def __init__(self):
        self.config = Config()
        self.app = QCoreApplication(sys.argv)
        self.worker: RecognitionWorker | None = None
        self.bridge: WebSocketBridge | None = None
        self.ws_server = get_server()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._shutdown = False

    def setup_worker(self) -> None:
        """Inicializa o worker de reconhecimento."""
        _LOG.info("Inicializando worker de reconhecimento...")
        
        # Criar componentes
        audio = AudioCapture(self.config)
        
        # IMPORTANTE: Inicializar AudioCapture antes de usar
        try:
            audio.initialize()
            _LOG.info("AudioCapture inicializado com sucesso")
        except Exception as exc:
            _LOG.error(f"Erro ao inicializar AudioCapture: {exc}", exc_info=True)
            raise
        
        # Criar recognizer instances
        audd = AudDRecognizer(self.config.get("API", "audd_api_key", fallback=""))
        acr = ACRCloudRecognizer(
            access_key=self.config.get("ACRCloud", "access_key", fallback=""),
            access_secret=self.config.get("ACRCloud", "access_secret", fallback=""),
            host=self.config.get("ACRCloud", "host", fallback="identify-eu-west-1.acrcloud.com"),
        )
        recognizer = MultiProviderRecognizer(
            audd=audd,
            acrcloud=acr,
            order=self.config.get("Recognition", "provider_fallback_order", fallback="acrcloud,audd"),
            attempts_per_provider=self.config.getint("Recognition", "provider_attempts", fallback=2),
        )
        
        lyrics = LyricsFetcher(self.config)
        
        # Criar worker
        self.worker = RecognitionWorker(
            self.config,
            audio,
            recognizer,
            lyrics
        )
        
        # Criar bridge WebSocket
        self.bridge = WebSocketBridge()
        
        # Conectar sinais do worker ao bridge
        self.worker.status_changed.connect(self.bridge.on_status_changed)
        self.worker.song_found.connect(self.bridge.on_song_found)
        self.worker.song_not_found.connect(self.bridge.on_song_not_found)
        self.worker.lyrics_ready.connect(self.bridge.on_lyrics_ready)
        self.worker.lyrics_not_found.connect(self.bridge.on_lyrics_not_found)
        self.worker.timecode_updated.connect(self.bridge.on_timecode_updated)
        self.worker.error_occurred.connect(self.bridge.on_error_occurred)
        
        _LOG.info("Worker configurado com sucesso")

    def run_websocket_server(self) -> None:
        """Roda o servidor WebSocket em thread separada."""
        _LOG.info("Iniciando servidor WebSocket...")
        
        # Criar novo event loop para esta thread
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Passar loop para o bridge
        if self.bridge:
            self.bridge.set_event_loop(self.loop)
        
        # Iniciar servidor
        self.loop.run_until_complete(self.ws_server.start())
        
        # Manter o loop rodando
        try:
            self.loop.run_forever()
        finally:
            # Cleanup ao parar
            self.loop.run_until_complete(self.ws_server.stop())
            self.loop.close()
            _LOG.info("Servidor WebSocket parado")

    def start(self) -> None:
        """Inicia o servidor backend."""
        _LOG.info("=" * 60)
        _LOG.info("Floating Lyrics - Backend Server Mode")
        _LOG.info("=" * 60)
        
        # Setup worker
        self.setup_worker()
        
        # Iniciar WebSocket em thread separada
        ws_thread = threading.Thread(target=self.run_websocket_server, daemon=True)
        ws_thread.start()
        
        # Aguardar um pouco para o WS server iniciar
        import time
        time.sleep(0.5)
        
        # Iniciar worker de reconhecimento
        if self.worker:
            _LOG.info("Iniciando reconhecimento de música...")
            self.worker.start()
        
        # Setup signal handlers para shutdown gracioso
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        
        _LOG.info("")
        _LOG.info("✅ Servidor rodando!")
        _LOG.info("   WebSocket: ws://127.0.0.1:8765/ws")
        _LOG.info("   Health: http://127.0.0.1:8765/health")
        _LOG.info("")
        _LOG.info("Pressione Ctrl+C para parar")
        _LOG.info("")
        
        # Rodar Qt event loop
        sys.exit(self.app.exec())

    def _handle_signal(self, signum, frame) -> None:
        """Handler para SIGINT/SIGTERM."""
        if self._shutdown:
            return
        
        _LOG.info("\n\nRecebido sinal de shutdown...")
        self._shutdown = True
        
        # Parar worker
        if self.worker:
            _LOG.info("Parando worker...")
            self.worker.stop()
            self.worker.wait(2000)
        
        # Parar WebSocket server
        if self.loop:
            _LOG.info("Parando WebSocket server...")
            self.loop.call_soon_threadsafe(self.loop.stop)
        
        # Sair do Qt
        QCoreApplication.quit()


def main():
    """Ponto de entrada principal."""
    server = BackendServer()
    server.start()


if __name__ == "__main__":
    main()
