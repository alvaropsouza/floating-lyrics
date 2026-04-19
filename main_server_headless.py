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
        logging.FileHandler(Path(__file__).parent / "server.log", encoding="utf-8"),
    ]
)
# Silenciar módulos muito verbosos
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

_LOG = logging.getLogger(__name__)

from config import Config
from src.audio_capture import AudioCapture
from src.lyrics_fetcher import LyricsFetcher
from src.song_recognition import (
    ACRCloudRecognizer,
    AudDRecognizer,
    LLMAPIRecognizer,
    LLMAPITrainer,
    LLMSearchClient,
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
        use_llm_for_recognition = self.config.getboolean(
            "Recognition",
            "use_llm_for_recognition",
            fallback=False,
        )
        llm_api = None
        if use_llm_for_recognition:
            llm_api = LLMAPIRecognizer(
                base_url=self.config.get("LLMApi", "base_url", fallback="http://127.0.0.1:3000"),
                api_key=self.config.get("API", "llm_api_key", fallback=""),
                top_k=self.config.getint("LLMApi", "top_k", fallback=3),
            )
        llm_trainer = LLMAPITrainer(
            base_url=self.config.get("LLMApi", "base_url", fallback="http://127.0.0.1:3000"),
            api_key=self.config.get("API", "llm_api_key", fallback=""),
        )
        recognizer = MultiProviderRecognizer(
            audd=audd,
            acrcloud=acr,
            llm_api=llm_api,
            order=self.config.get("Recognition", "provider_fallback_order", fallback="acrcloud,audd"),
            attempts_per_provider=self.config.getint("Recognition", "provider_attempts", fallback=2),
        )
        
        lyrics = LyricsFetcher(self.config)
        
        # Cliente /search (busca web + LLM → metadados + letras)
        llm_search = LLMSearchClient(
            base_url=self.config.get("LLMApi", "base_url", fallback="http://127.0.0.1:3000"),
        )

        # Criar worker headless
        self.worker = RecognitionWorkerHeadless(
            self.config,
            audio,
            recognizer,
            lyrics,
            llm_trainer=llm_trainer,
            llm_search=llm_search,
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
        
        # Aguardar indefinidamente (até receber SIGINT/SIGTERM)
        try:
            await asyncio.Event().wait()  # Espera eterna
        except asyncio.CancelledError:
            _LOG.info("\nShutdown solicitado...")
            await self.shutdown()
            return

    async def shutdown(self) -> None:
        """Shutdown gracioso."""
        if self._shutdown:
            return
        
        _LOG.info("Parando servidor...")
        self._shutdown = True
        
        # Parar worker
        if self.worker:
            _LOG.info("Parando worker...")
            self.worker.stop()
            self.worker.join(timeout=2)
        
        # Parar WebSocket server
        _LOG.info("Parando WebSocket server...")
        await self.ws_server.stop()
        
        _LOG.info("Servidor parado com sucesso")

    def run(self) -> int:
        """Entry point principal."""
        # Configurar signal handlers
        def signal_handler(sig, frame):
            _LOG.info(f"\nRecebido sinal {sig}")
            if self.loop:
                # Cancelar todas as tasks
                for task in asyncio.all_tasks(self.loop):
                    task.cancel()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Rodar servidor
        try:
            asyncio.run(self.run_server())
        except asyncio.CancelledError:
            _LOG.info("Servidor encerrado.")
        except KeyboardInterrupt:
            _LOG.info("\nInterrompido pelo usuário")
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
