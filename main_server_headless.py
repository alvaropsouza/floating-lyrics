"""
Modo servidor headless: Backend Python 100% sem PyQt6.

Usa apenas asyncio + threading para reconhecimento de música e WebSocket.
Ideal para usar com o frontend Flutter.

Uso:
    python main_server_headless.py           # normal
    python main_server_headless.py --reload  # com auto-reload ao salvar arquivos
"""

import asyncio
import logging
import signal
import socket
import sys
import time
from pathlib import Path

# ── Logging colorido com Rich ────────────────────────────────────────────────
from rich.logging import RichHandler
from rich.console import Console
from rich.theme import Theme

_console = Console(theme=Theme({
    "logging.level.debug":   "dim cyan",
    "logging.level.info":    "bold green",
    "logging.level.warning": "bold yellow",
    "logging.level.error":   "bold red",
    "logging.level.critical":"bold white on red",
}))

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%H:%M:%S]",
    handlers=[
        RichHandler(
            console=_console,
            rich_tracebacks=True,
            show_path=False,
            markup=True,
        ),
        logging.FileHandler(Path(__file__).parent / "logs" / "server.log", encoding="utf-8"),
    ]
)
# Silenciar módulos muito verbosos
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("faster_whisper").setLevel(logging.WARNING)
logging.getLogger("silero_vad").setLevel(logging.WARNING)
logging.getLogger("ctranslate2").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_LOG = logging.getLogger(__name__)

from config import Config
from src.audio_capture import AudioCapture
from src.lyrics_fetcher import LyricsFetcher
from src.song_recognition import (
    ACRCloudRecognizer,
    AcoustIDRecognizer,
    AudDRecognizer,
    MultiProviderRecognizer,
)
from src.websocket_bridge_headless import WebSocketBridgeHeadless
from src.websocket_server import get_server
from src.worker_headless import RecognitionWorkerHeadless


def is_port_in_use(host: str, port: int) -> bool:
    """Verifica se uma porta está em uso."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def find_available_port(host: str, start_port: int, max_attempts: int = 10) -> int | None:
    """Encontra uma porta disponível a partir de start_port."""
    for port in range(start_port, start_port + max_attempts):
        if not is_port_in_use(host, port):
            return port
    return None


class HeadlessBackendServer:
    """Servidor backend 100% headless (sem PyQt6)."""

    def __init__(self):
        self.config = Config()
        self.worker: RecognitionWorkerHeadless | None = None
        self.bridge: WebSocketBridgeHeadless | None = None
        self.ws_server = get_server()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._shutdown = False

    def setup_worker(self) -> None:
        """Inicializa o worker de reconhecimento."""
        _LOG.info("Inicializando worker de reconhecimento...")
        
        # Criar componentes
        audio = AudioCapture(self.config)
        
        # Inicializar AudioCapture
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
        acoustid = AcoustIDRecognizer(
            api_key=self.config.get("API", "acoustid_api_key", fallback=""),
        )
        
        recognizer = MultiProviderRecognizer(
            audd=audd,
            acrcloud=acr,
            acoustid=acoustid,
            order=self.config.get("Recognition", "provider_fallback_order", fallback="acrcloud,audd,acoustid"),
            attempts_per_provider=self.config.getint("Recognition", "provider_attempts", fallback=1),
        )
        
        lyrics = LyricsFetcher(self.config)

        # Criar worker headless
        self.worker = RecognitionWorkerHeadless(
            self.config,
            audio,
            recognizer,
            lyrics,
        )
        
        _LOG.info("Worker headless configurado com sucesso")

    async def run_server(self) -> None:
        """Roda o servidor WebSocket e worker."""
        _LOG.info("=" * 60)
        _LOG.info("Floating Lyrics - Headless Backend Server")
        _LOG.info("=" * 60)
        
        # Setup worker
        self.setup_worker()
        
        # Verificar se a porta padrão está disponível
        default_port = 8765
        host = "127.0.0.1"
        
        if is_port_in_use(host, default_port):
            _LOG.warning(f"⚠️  Porta {default_port} já está em uso!")
            _LOG.info("🔍 Procurando porta alternativa...")
            
            alternative_port = find_available_port(host, default_port + 1)
            if alternative_port:
                _LOG.info(f"✓ Porta {alternative_port} disponível, usando-a...")
                # Recriar servidor com porta alternativa
                from src.websocket_server import WebSocketServer
                from src import websocket_server
                websocket_server._server_instance = WebSocketServer(host, alternative_port)
                self.ws_server = websocket_server._server_instance
            else:
                _LOG.error("❌ Nenhuma porta disponível encontrada!")
                _LOG.error("💡 Dica: Feche outros servidores Python rodando com:")
                _LOG.error("   taskkill //F //IM python.exe")
                raise RuntimeError("Nenhuma porta disponível para o servidor WebSocket")
        
        # Iniciar servidor WebSocket
        _LOG.info("Iniciando servidor WebSocket...")
        try:
            await self.ws_server.start()
        except OSError as exc:
            if exc.errno == 10048:  # Port already in use
                _LOG.error("❌ Erro: Porta ainda em uso")
                _LOG.error("💡 Execute: taskkill //F //IM python.exe")
                raise
            raise
        
        # Criar bridge (passa o event loop atual)
        self.loop = asyncio.get_running_loop()
        self.bridge = WebSocketBridgeHeadless(self.worker, self.loop, self.config)
        self.ws_server.set_command_handler(self._handle_ws_command)
        
        # Configurar shutdown handler com asyncio (funciona melhor no Windows)
        shutdown_event = asyncio.Event()
        
        def request_shutdown():
            _LOG.info("\n🛑 Interrupção detectada (Ctrl+C)")
            shutdown_event.set()
        
        # Registrar signal handlers no event loop (não usa signal.signal diretamente)
        try:
            self.loop.add_signal_handler(signal.SIGINT, request_shutdown)
            self.loop.add_signal_handler(signal.SIGTERM, request_shutdown)
        except NotImplementedError:
            # Windows não suporta add_signal_handler, então vamos usar outra abordagem
            # Vamos capturar KeyboardInterrupt no nível do asyncio.run()
            pass
        
        # Iniciar worker em thread separada
        _LOG.info("Iniciando reconhecimento de música...")
        self.worker.start()
        
        # Obter porta e host reais do servidor
        actual_port = self.ws_server.port
        actual_host = self.ws_server.host
        
        _LOG.info("")
        _LOG.info("✅ Servidor rodando!")
        _LOG.info(f"   WebSocket: ws://{actual_host}:{actual_port}/ws")
        _LOG.info(f"   Health: http://{actual_host}:{actual_port}/health")
        _LOG.info("")
        _LOG.info("Pressione Ctrl+C para parar")
        _LOG.info("")
        
        # Aguardar até shutdown ser solicitado
        await shutdown_event.wait()
        await self.shutdown()

    async def _handle_ws_command(self, command: str, _payload: dict) -> dict:
        """Processa comandos de controle recebidos pelo WebSocket."""
        if not self.worker:
            return {
                "ok": False,
                "message": "Worker indisponível",
            }

        if command == "pause":
            self.worker.pause()
            await self.ws_server.emit_status("⏸️ Reconhecimento pausado")
            return {"ok": True, "is_paused": True}

        if command == "resume":
            self.worker.resume()
            await self.ws_server.emit_status("▶️ Retomando reconhecimento...")
            return {"ok": True, "is_paused": False}

        if command == "toggle_pause":
            paused = self.worker.toggle_pause()
            status = "⏸️ Reconhecimento pausado" if paused else "▶️ Retomando reconhecimento..."
            await self.ws_server.emit_status(status)
            return {"ok": True, "is_paused": paused}

        if command == "get_runtime_status":
            return {
                "ok": True,
                "is_paused": self.worker.is_paused(),
                "is_debug_only": self.worker.is_debug_only(),
            }

        if command == "debug_only":
            enabled = bool(_payload.get("enabled", True))
            self.worker.set_debug_only(enabled)
            status = "🔧 Modo debug: capturando áudio sem enviar para APIs" if enabled else "▶️ Retomando reconhecimento normal..."
            await self.ws_server.emit_status(status)
            return {"ok": True, "is_debug_only": enabled, "is_paused": False}

        return {
            "ok": False,
            "message": f"Comando desconhecido: {command}",
        }

    async def shutdown(self) -> None:
        """Shutdown gracioso."""
        if self._shutdown:
            return
        
        _LOG.info("Parando servidor...")
        self._shutdown = True
        
        # Parar worker com timeout reduzido (3s é suficiente para interromper captura)
        if self.worker:
            _LOG.info("Parando worker...")
            self.worker.stop()
            # Aguardar até 3 segundos (captura de áudio agora pode ser interrompida)
            self.worker.join(timeout=3)
            if self.worker.is_alive():
                _LOG.warning("⚠️  Worker não parou a tempo")
                _LOG.info("   Forçando encerramento do processo...")
                # Não aguardar mais - o processo terminará de qualquer forma
        
        # Parar WebSocket server (com timeout)
        _LOG.info("Parando WebSocket server...")
        try:
            await asyncio.wait_for(self.ws_server.stop(), timeout=2.0)
        except asyncio.TimeoutError:
            _LOG.warning("WebSocket server não parou a tempo")
        
        _LOG.info("✓ Servidor parado")

    def run(self) -> int:
        """Entry point principal."""
        # Rodar servidor
        try:
            asyncio.run(self.run_server())
        except KeyboardInterrupt:
            # No Windows, asyncio.run() captura KeyboardInterrupt
            # Precisamos garantir que shutdown() seja chamado
            _LOG.info("\n🛑 Interrompido pelo usuário (Ctrl+C)")
            
            # Forçar cleanup síncrono com timeout reduzido
            if self.worker and self.worker.is_alive():
                _LOG.info("Parando worker...")
                self.worker.stop()
                self.worker.join(timeout=3)
                if self.worker.is_alive():
                    _LOG.warning("⚠️  Worker não parou a tempo")
                    _LOG.info("   Forçando encerramento do processo...")
                    # Não aguardar mais
                    
            _LOG.info("✓ Servidor parado")
            return 0
        except RuntimeError as exc:
            # Erros de configuração (porta em uso, etc)
            _LOG.error(f"\n❌ Erro: {exc}")
            return 1
        except Exception as exc:
            _LOG.error(f"\n❌ Erro fatal: {exc}", exc_info=True)
            return 1
        
        return 0


def main() -> int:
    if "--reload" in sys.argv:
        return _run_with_reload()
    server = HeadlessBackendServer()
    return server.run()


def _run_with_reload() -> int:
    """Reinicia o servidor automaticamente ao detectar mudanças em .py."""
    import subprocess
    from watchfiles import watch

    src_dir = Path(__file__).parent
    watch_paths = [str(src_dir / "src"), str(src_dir / "main_server_headless.py")]

    _LOG.info("[bold cyan]🔄 Modo auto-reload ativo[/bold cyan] — monitorando [dim]src/[/dim]")
    _LOG.info("   Salve qualquer [yellow].py[/yellow] para reiniciar o servidor automaticamente.\n")

    args = [sys.executable, str(Path(__file__).resolve())]

    proc = subprocess.Popen(args)
    try:
        for _ in watch(*watch_paths, yield_on_timeout=False):
            _LOG.info("[yellow]🔄 Mudança detectada — reiniciando servidor...[/yellow]")
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc = subprocess.Popen(args)
    except KeyboardInterrupt:
        _LOG.info("\n[red]Parando watcher...[/red]")
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
