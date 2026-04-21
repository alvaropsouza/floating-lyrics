"""
Monitor do Windows System Media Transport Controls (SMTC).

Detecta troca de faixa pelo metadata reportado pelos players (Spotify, WMP,
navegadores, etc.) ao sistema operacional via SMTC, complementando (e acelerando)
a detecção local por silêncio/espectro.

Suporte: Windows 10 build 1803+ (RS4).

Instalação (opcional — o módulo degrada graciosamente se não disponível):
  pip install winsdk
  # ou
  pip install winrt-runtime "winrt-Windows.Media.Control"
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Callable, Optional

_LOG = logging.getLogger(__name__)

# ── Importação condicional dos bindings WinRT ────────────────────────────────
# Tenta winsdk primeiro (pacote único) e cai para os pacotes split winrt-*.
_SMTC_AVAILABLE = False
_Manager = None  # GlobalSystemMediaTransportControlsSessionManager

if sys.platform == "win32":
    try:
        from winsdk.windows.media.control import (  # type: ignore[import]
            GlobalSystemMediaTransportControlsSessionManager as _Manager,
        )
        _SMTC_AVAILABLE = True
        _LOG.debug("SmtcMonitor: usando winsdk")
    except ImportError:
        pass

    if not _SMTC_AVAILABLE:
        try:
            from winrt.windows.media.control import (  # type: ignore[import]
                GlobalSystemMediaTransportControlsSessionManager as _Manager,
            )
            _SMTC_AVAILABLE = True
            _LOG.debug("SmtcMonitor: usando winrt-runtime")
        except ImportError:
            pass


def smtc_available() -> bool:
    """Retorna True se o pacote winrt/winsdk está instalado e disponível."""
    return _SMTC_AVAILABLE


class SmtcMonitor:
    """
    Monitora o SMTC do Windows em background e chama callbacks quando a faixa muda.

    Funciona com qualquer player que reporte metadata ao SMTC:
    Spotify, Windows Media Player, Groove Music, navegadores (YouTube, Deezer etc).

    Uso::

        monitor = SmtcMonitor(poll_interval_s=1.0)
        monitor.on_track_changed(lambda title, artist: print(title, artist))
        monitor.start()
        # ... quando quiser parar:
        monitor.stop()
    """

    def __init__(self, poll_interval_s: float = 1.0) -> None:
        self._poll_interval = poll_interval_s
        self._callbacks: list[Callable[[str, str], None]] = []
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Último par (título.lower(), artista.lower()) visto; None = sem sessão
        self._last_key: Optional[tuple[str, str]] = None

    # ── API pública ──────────────────────────────────────────────────────────

    def on_track_changed(self, callback: Callable[[str, str], None]) -> None:
        """Registra callback chamado quando a faixa muda.

        Assinatura: ``callback(title: str, artist: str)``
        Chamado a partir de uma thread de background; sincronize acessos se necessário.
        """
        self._callbacks.append(callback)

    def start(self) -> bool:
        """Inicia o monitor em thread de background.

        Retorna False (sem lançar exceção) se SMTC não estiver disponível —
        o worker pode continuar normalmente sem o monitor.
        """
        if not _SMTC_AVAILABLE:
            _LOG.warning(
                "SmtcMonitor: pacote winsdk/winrt não encontrado — monitoramento SMTC desativado. "
                "Instale com: pip install winsdk"
            )
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_in_thread,
            daemon=True,
            name="SmtcMonitor",
        )
        self._thread.start()
        _LOG.info("SmtcMonitor iniciado (intervalo=%.1fs)", self._poll_interval)
        return True

    def stop(self) -> None:
        """Sinaliza parada. Retorna imediatamente; a thread encerra no próximo ciclo."""
        self._stop_event.set()

    # ── Internals ────────────────────────────────────────────────────────────

    def _run_in_thread(self) -> None:
        """Cria event loop próprio (necessário para WinRT) e executa o poll loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._poll_loop())
        except Exception as exc:
            _LOG.error("SmtcMonitor encerrado com erro inesperado: %s", exc, exc_info=True)
        finally:
            loop.close()
            _LOG.info("SmtcMonitor parado")

    async def _poll_loop(self) -> None:
        """Consulta SMTC periodicamente e dispara callbacks em mudanças de faixa."""
        while not self._stop_event.is_set():
            try:
                track = await self._get_current_track()
                if track is not None:
                    self._check_and_fire(track)
            except Exception as exc:
                _LOG.debug("SmtcMonitor: erro ao consultar SMTC: %s", exc)

            await asyncio.sleep(self._poll_interval)

    def _check_and_fire(self, track: tuple[str, str]) -> None:
        """Compara a faixa recebida com a última conhecida e dispara callbacks se mudou."""
        title, artist = track
        key = (title.casefold(), artist.casefold())
        if key == self._last_key:
            return
        prev = self._last_key
        self._last_key = key
        _LOG.info(
            "SMTC: faixa alterada para '%s' — '%s' (antes: '%s' — '%s')",
            title,
            artist,
            prev[0] if prev else "–",
            prev[1] if prev else "–",
        )
        self._fire_callbacks(title, artist)

    async def _get_current_track(self) -> Optional[tuple[str, str]]:
        """
        Retorna (título, artista) da sessão SMTC atual.

        Retorna None se nenhum player estiver ativo ou se o title estiver vazio.
        """
        try:
            manager = await _Manager.request_async()
        except Exception as exc:
            _LOG.debug("SmtcMonitor: falha ao criar manager: %s", exc)
            return None

        session = manager.get_current_session()
        if session is None:
            return None

        try:
            props = await session.try_get_media_properties_async()
        except Exception as exc:
            _LOG.debug("SmtcMonitor: falha ao obter propriedades: %s", exc)
            return None

        if props is None:
            return None

        title = (props.title or "").strip()
        artist = (props.artist or "").strip()

        if not title:
            return None

        return title, artist

    def _fire_callbacks(self, title: str, artist: str) -> None:
        for cb in self._callbacks:
            try:
                cb(title, artist)
            except Exception as exc:
                _LOG.error(
                    "SmtcMonitor: erro no callback para faixa '%s' — '%s': %s",
                    title,
                    artist,
                    exc,
                )
