"""
Background worker headless (sem PyQt6) para usar com WebSocket.

Roda em thread separada e usa callbacks ao invés de Qt signals.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Callable, Any

from src.song_recognition import RateLimitError

# SMTC Monitor (Windows - detecção de troca de faixa via OS)
try:
    from src.smtc_monitor import SmtcMonitor, smtc_available
    _SMTC_IMPORTED = True
except ImportError:
    _SMTC_IMPORTED = False
    SmtcMonitor = None  # type: ignore[assignment,misc]
    def smtc_available() -> bool:  # type: ignore[misc]
        return False

# STT Sync (Fase 2) - Importação condicional
try:
    from src.speech_recognition import SpeechRecognizer
    from src.lyrics_matcher import LyricsMatcher
    _STT_AVAILABLE = True
except (ImportError, OSError):
    # OSError cobre falhas de DLL do Windows (ex: c10.dll do PyTorch não carrega)
    _STT_AVAILABLE = False
    SpeechRecognizer = None
    LyricsMatcher = None


_LOG = logging.getLogger(__name__)


class RecognitionWorkerHeadless(threading.Thread):
    """
    Worker de reconhecimento sem dependências PyQt6.
    
    Infinite loop:
      1. Capture system audio (WASAPI loopback)
      2. Send to recognition APIs
      3. Fetch lyrics if the song changed
      4. Chama callbacks para notificar eventos
    """

    def __init__(self, config, audio_capture, recognizer, lyrics_fetcher):
        super().__init__(daemon=True, name="RecognitionWorker")
        self._config = config
        self._audio = audio_capture
        self._recognizer = recognizer
        self._lyrics = lyrics_fetcher
        self._stop_flag = threading.Event()
        self._pause_flag = threading.Event()
        self._debug_only_flag = threading.Event()
        # Sinalizado por fontes externas (ex: SMTC) para acordar o loop imediatamente
        self._wake_event = threading.Event()
        self._pause_status_emitted = False
        self._current_song_key: tuple[str, str, str] | None = None
        self._miss_streak: int = 0
        self._lyrics_cache: dict[tuple[str, str, str], tuple[str, bool] | None] = {}
        self._rate_limit_until: float = 0.0
        self._rate_limit_cooldown_s: float = 30 * 60  # 30 minutes
        self._last_recognition_time: float = 0.0
        self._current_song_duration_s: float = 0.0

        # Conectar flag de stop ao AudioCapture para interromper capturas em andamento
        self._audio.set_shutdown_flag(self._stop_flag)

        self._miss_reset_threshold = max(
            1,
            self._config.getint("Recognition", "tracking_miss_reset", fallback=3),
        )
        
        # Callbacks para eventos
        self._callbacks: dict[str, list[Callable]] = {
            'status_changed': [],
            'song_found': [],
            'song_not_found': [],
            'lyrics_loading': [],
            'lyrics_ready': [],
            'lyrics_not_found': [],
            'timecode_updated': [],
            'error_occurred': [],
            'audio_spectrum': [],  # Novo evento para espectro de áudio
        }
        
        # Thread separada para captura de espectro em tempo real
        self._spectrum_thread: threading.Thread | None = None
        self._spectrum_running = False
        
        # ThreadPool para busca de letras não bloqueante
        self._lyrics_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="LyricsFetch")
        self._pending_lyrics_future: Future | None = None

        # Detector local de troca de musica (evita chamadas as APIs durante tracking)
        self._disable_api_recognition_while_tracking = self._config.getboolean(
            "Recognition",
            "disable_api_recognition_while_tracking",
            fallback=True,
        )
        # Intervalo de um frame do loop de espectro (~30 FPS)
        _FRAME_DT_S = 0.033

        self._change_similarity_threshold = self._config.getfloat(
            "Recognition",
            "local_change_similarity_threshold",
            fallback=0.72,
        )
        _window_s = self._config.getfloat(
            "Recognition", "local_change_window_s", fallback=0.7
        )
        self._change_frames_threshold = max(1, round(_window_s / _FRAME_DT_S))
        self._change_min_energy = self._config.getfloat(
            "Recognition",
            "local_change_min_energy",
            fallback=0.08,
        )
        self._change_cooldown_s = max(
            0,
            self._config.getfloat("Recognition", "local_change_cooldown_s", fallback=5.0),
        )
        # Janela deslizante de frames: bool (True = frame divergente)
        self._change_window_size = self._change_frames_threshold
        self._change_window: list[bool] = []
        self._change_baseline: list[float] | None = None
        self._last_change_trigger_at = 0.0

        # Detecção silence→audio (gatilho mais confiável que espectro)
        self._silence_frames = 0
        _trigger_s = self._config.getfloat(
            "Recognition", "silence_trigger_s", fallback=0.7
        )
        self._silence_trigger_frames = max(1, round(_trigger_s / _FRAME_DT_S))
        self._silence_triggered = False  # True = estava em silêncio, esperando nova música
        self._post_silence_audio_frames = 0
        _recovery_s = self._config.getfloat(
            "Recognition", "silence_recovery_s", fallback=0.3
        )
        self._silence_recovery_frames = max(1, round(_recovery_s / _FRAME_DT_S))
        self._silence_recovery_energy_multiplier = self._config.getfloat(
            "Recognition",
            "silence_recovery_energy_multiplier",
            fallback=1.8,
        )


        
        # Detecção de confiança baixa
        self._last_confidence: float | None = None
        self._low_confidence_count = 0  # Contador de reconhecimentos com confiança baixa
        self._low_confidence_threshold = self._config.getfloat("Recognition", "confidence_threshold", fallback=0.5)
        self._low_confidence_trigger = max(1, self._config.getint("Recognition", "confidence_trigger", fallback=2))
        
        # Rastreamento de mudanças rápidas
        self._min_recognition_interval_s = self._config.getfloat("Recognition", "confidence_interval", fallback=2.0)
        self._last_full_recognition_time: float = 0.0
        self._new_song_attempts: int = 0
        self._new_song_cooldown_until: float = 0.0
        
        # Configurar callback de captura fresca no recognizer
        self._recognizer.set_fresh_capture_callback(self._capture_fresh_audio)

        # ─ STT Sync Integration (Fase 2) ──────────────────────────────────
        self._stt_enabled = False
        self._stt_mode = "timestamp_only"  # timestamp_only | stt_only | hybrid
        self._stt_recognizer: SpeechRecognizer | None = None
        self._stt_matcher: LyricsMatcher | None = None
        self._stt_thread: threading.Thread | None = None
        self._stt_running = False
        self._stt_lock = threading.Lock()
        self._current_lyrics_for_stt: list[str] | None = None
        self._current_lyrics_index: int = 0
        self._stt_last_segment = None
        
        # Ler configurações STT de config.ini (init do modelo é lazy — roda na thread do worker)
        if _STT_AVAILABLE:
            self._stt_enabled = self._config.getboolean("SpeechSync", "enabled", fallback=False)
            self._stt_mode = self._config.get("SpeechSync", "mode", fallback="timestamp_only")
        
        # Callbacks para STT (adicionados aos callbacks existentes)
        self._callbacks.setdefault('stt_recognized', [])
        self._callbacks.setdefault('stt_matched', [])
        self._callbacks.setdefault('sync_corrected', [])

        # ─ SMTC Monitor (Windows track-change detection) ───────────────────
        self._smtc_monitor: SmtcMonitor | None = None
        self._smtc_enabled = False
        if _SMTC_IMPORTED:
            self._smtc_enabled = self._config.getboolean(
                "Recognition", "smtc_enabled", fallback=True
            )
            if self._smtc_enabled:
                poll_s = self._config.getfloat(
                    "Recognition", "smtc_poll_interval_s", fallback=1.0
                )
                self._smtc_monitor = SmtcMonitor(poll_interval_s=poll_s)
                self._smtc_monitor.on_track_changed(self._on_smtc_track_changed)

    @staticmethod
    def _sanitize_part(value: str, fallback: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return fallback
        normalized = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
        normalized = normalized.lower()
        normalized = re.sub(r"\s+", "-", normalized)
        normalized = re.sub(r"[^a-z0-9._-]", "", normalized)
        normalized = normalized.strip("-._")
        return normalized or fallback

    # ── Event callbacks ─────────────────────────────────────────────────────

    def on(self, event: str, callback: Callable) -> None:
        """Registra callback para um evento."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _emit(self, event: str, *args, **kwargs) -> None:
        """Dispara todos os callbacks registrados para um evento."""
        for callback in self._callbacks.get(event, []):
            try:
                callback(*args, **kwargs)
            except Exception as exc:
                _LOG.error(f"Erro no callback {event}: {exc}", exc_info=True)

    # ── Control ─────────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Para a thread."""
        self._spectrum_running = False
        self._stop_flag.set()
        self._wake_event.set()  # acorda _wait_interval imediatamente para responder ao stop
        self._pause_flag.clear()

        # Parar SMTC monitor
        if self._smtc_monitor is not None:
            self._smtc_monitor.stop()
        
        # 🎤 Parar STT sync
        self._stop_stt_loop()
        
        # Cancelar busca de letras pendente e aguardar shutdown
        if self._pending_lyrics_future and not self._pending_lyrics_future.done():
            self._pending_lyrics_future.cancel()
        self._lyrics_executor.shutdown(wait=False, cancel_futures=True)

    def join(self, timeout=None) -> None:
        """Aguarda a thread terminar."""
        if self._spectrum_thread and self._spectrum_thread.is_alive():
            self._spectrum_thread.join(timeout=2)
        super().join(timeout)

    def pause(self) -> None:
        """Pausa ciclos de reconhecimento e captura de espectro."""
        self._pause_flag.set()
        self._debug_only_flag.clear()

    def resume(self) -> None:
        """Retoma ciclos de reconhecimento e captura de espectro."""
        self._pause_flag.clear()
        self._debug_only_flag.clear()
        self._pause_status_emitted = False
        self._emit('status_changed', "▶️ Retomando reconhecimento...")

    def toggle_pause(self) -> bool:
        """Alterna estado de pausa e retorna estado final (True=pausado)."""
        if self._pause_flag.is_set():
            self.resume()
            return False
        self.pause()
        return True

    def is_paused(self) -> bool:
        """Retorna True quando o worker está pausado."""
        return self._pause_flag.is_set()

    def set_debug_only(self, enabled: bool) -> None:
        """Modo debug: captura áudio e salva WAV mas não envia para APIs."""
        if enabled:
            self._pause_flag.clear()
            self._debug_only_flag.set()
            self._pause_status_emitted = False
            self._emit('status_changed', "🔧 Modo debug: capturando áudio sem enviar para APIs")
        else:
            self._debug_only_flag.clear()
            self._emit('status_changed', "▶️ Retomando reconhecimento normal...")

    def is_debug_only(self) -> bool:
        """Retorna True quando em modo debug (captura sem API)."""
        return self._debug_only_flag.is_set()

    # ── Thread entry point ──────────────────────────────────────────────────

    def run(self) -> None:
        """Loop principal de reconhecimento."""
        _LOG.info("Worker headless iniciado")
        
        # Iniciar thread de captura de espectro
        self._spectrum_running = True
        self._spectrum_thread = threading.Thread(
            target=self._spectrum_loop,
            daemon=True,
            name="SpectrumCapture"
        )
        self._spectrum_thread.start()

        # Iniciar monitor SMTC (detecção instantânea de troca de faixa via Windows OS)
        if self._smtc_monitor is not None:
            ok = self._smtc_monitor.start()
            if ok:
                _LOG.info("SmtcMonitor ativo — trocas de faixa detectadas via SMTC do Windows")
            else:
                _LOG.warning(
                    "SmtcMonitor não pôde iniciar — pacote winsdk/winrt não instalado. "
                    "Instale com: pip install winsdk"
                )

        # Iniciar thread STT em background — o modelo Whisper é carregado dentro da thread
        # (nunca no event loop asyncio) e a thread fica em idle até as letras chegarem
        if _STT_AVAILABLE and self._stt_enabled:
            self._stt_thread = threading.Thread(
                target=self._stt_init_and_loop,
                daemon=True,
                name="STTSync",
            )
            self._stt_thread.start()
            _LOG.info("🎤 STT thread iniciada (aguardando carregamento do modelo...)")
        
        while not self._stop_flag.is_set():
            if self._pause_flag.is_set():
                if not self._pause_status_emitted:
                    self._emit('status_changed', "⏸️ Reconhecimento pausado")
                    self._pause_status_emitted = True
                self._stop_flag.wait(timeout=0.2)
                continue

            self._pause_status_emitted = False
            try:
                self._cycle()
            except Exception as exc:
                _LOG.error(f"Erro no ciclo de reconhecimento: {exc}", exc_info=True)
            
            if self._stop_flag.is_set():
                break
            self._wait_interval()
        
        # Parar thread de espectro
        self._spectrum_running = False
        _LOG.debug("Worker: loop principal encerrado")
        _LOG.info("Worker headless parado")

    def _spectrum_loop(self) -> None:
        """Loop contínuo de emissão de espectro. Nunca para por erros — apenas pelo stop_flag."""
        _LOG.info("🎵 Thread de espectro iniciada (~30 FPS)")

        while self._spectrum_running and not self._stop_flag.is_set():
            if self._pause_flag.is_set():
                # Zera visualizador durante pausa para reforçar feedback na UI.
                self._emit('audio_spectrum', [0.0] * 32)
                self._stop_flag.wait(timeout=0.2)
                continue

            try:
                # capture_spectrum() apenas lê _last_spectrum pré-computado pelo SpectrumReader
                spectrum = self._audio.capture_spectrum(num_bars=32)
                self._emit('audio_spectrum', spectrum)
                # Detector de silêncio/mudança de música baseado no espectro
                self._update_local_change_detector(spectrum)
            except Exception as exc:
                _LOG.debug("Erro no loop de espectro (continuando): %s", exc)

            # ~30 FPS; usa wait() para responder ao stop_flag imediatamente
            self._stop_flag.wait(timeout=0.033)

        _LOG.info("Thread de espectro parada")

    def _check_silence_and_reset(self, spectrum: list[float]) -> None:
        # Backwards-compat shim: keep the old name but use the new detector.
        self._update_local_change_detector(spectrum)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        n = min(len(a), len(b))
        dot = 0.0
        na = 0.0
        nb = 0.0
        for i in range(n):
            va = float(a[i])
            vb = float(b[i])
            dot += va * vb
            na += va * va
            nb += vb * vb
        denom = (na ** 0.5) * (nb ** 0.5)
        if denom <= 1e-9:
            return 0.0
        return max(0.0, min(1.0, dot / denom))

    @staticmethod
    def _normalize_spectrum(vec: list[float]) -> list[float]:
        if not vec:
            return []
        norm = 0.0
        out = [0.0] * len(vec)
        for i, v in enumerate(vec):
            fv = float(v)
            out[i] = fv
            norm += fv * fv
        norm = norm ** 0.5
        if norm <= 1e-9:
            return out
        inv = 1.0 / norm
        for i in range(len(out)):
            out[i] *= inv
        return out

    def _reset_song_state(self, reason: str) -> None:
        """Limpa estado de música atual e emite evento para forçar redetecção."""
        _LOG.info("Mudanca de musica detectada (%s). Forcando redeteccao.", reason)
        self._last_change_trigger_at = time.monotonic()
        self._current_song_key = None
        self._current_song_duration_s = 0.0
        self._miss_streak = 0
        self._last_confidence = None
        self._low_confidence_count = 0
        self._change_baseline = None
        self._change_window = []
        self._silence_triggered = False
        self._post_silence_audio_frames = 0
        # Limpar letras STT para não sincronizar contra música anterior
        with self._stt_lock:
            self._current_lyrics_for_stt = None
            self._current_lyrics_index = 0
        self._emit('song_not_found')

    def _on_smtc_track_changed(self, smtc_title: str, smtc_artist: str) -> None:
        """Callback chamado pelo SmtcMonitor quando o Windows detecta troca de faixa.

        Roda em thread de background do SmtcMonitor — apenas redefine o estado
        e deixa o ciclo de reconhecimento detectar e confirmar a nova faixa.
        """
        if self._pause_flag.is_set() or self._stop_flag.is_set():
            return

        # Verificar se já estamos rastreando exatamente esta faixa
        if self._current_song_key is not None:
            cur_title, cur_artist, _ = self._current_song_key
            if (
                cur_title.casefold() == smtc_title.casefold()
                and cur_artist.casefold() == smtc_artist.casefold()
            ):
                _LOG.debug(
                    "SMTC: confirmação de faixa já rastreada '%s' — '%s', ignorando",
                    smtc_title,
                    smtc_artist,
                )
                return

        # Nova faixa detectada pelo OS → forçar redetecção e acordar o loop imediatamente
        self._reset_song_state(
            f"SMTC: nova faixa detectada pelo Windows — '{smtc_title}' — '{smtc_artist}'"
        )
        self._wake_event.set()

    def _emit_compensated_timecode(self, timecode_ms: int, capture_end: float, context: str) -> None:
        """Emite timecode compensado pela latência de rede/processamento.

        `capture_end` deve ser o ponto de referência que corresponde ao instante
        em que o último sample de áudio foi gerado pelo hardware — calculado como:
            audio.last_audio_end_time - audio.last_input_latency_s
        Isso elimina o overhead de cleanup do stream e a latência de buffer WASAPI.
        """
        if timecode_ms <= 0:
            return
        elapsed_ms = int((time.perf_counter() - capture_end) * 1000)
        compensated_timecode = timecode_ms + elapsed_ms
        _LOG.info(
            "%s: play_offset=%dms + proc/rede=%dms = %dms",
            context,
            timecode_ms,
            elapsed_ms,
            compensated_timecode,
        )
        self._emit('timecode_updated', compensated_timecode, capture_end)

    def _update_local_change_detector(self, spectrum: list[float]) -> None:
        if self._current_song_key is None:
            # Sem música ativa: manter apenas detecção de silêncio para não disparar desnecessariamente
            self._change_baseline = None
            self._change_window = []
            self._post_silence_audio_frames = 0
            avg_energy = sum(spectrum) / len(spectrum) if spectrum else 0.0
            if avg_energy < self._change_min_energy:
                self._silence_frames += 1
            else:
                self._silence_frames = 0
                self._silence_triggered = False
            return

        now = time.monotonic()
        in_cooldown = self._change_cooldown_s > 0 and (now - self._last_change_trigger_at) < self._change_cooldown_s

        avg_energy = sum(spectrum) / len(spectrum) if spectrum else 0.0
        is_silent = avg_energy < self._change_min_energy

        # ── Detecção silêncio → áudio ───────────────────────────────────────
        if is_silent:
            self._silence_frames += 1
            self._post_silence_audio_frames = 0
            if self._silence_frames >= self._silence_trigger_frames:
                self._silence_triggered = True  # armar o gatilho
        else:
            if self._silence_triggered:
                # Evita falso positivo: exige volta consistente de energia para confirmar troca.
                recovery_energy = self._change_min_energy * max(1.0, self._silence_recovery_energy_multiplier)
                if avg_energy >= recovery_energy:
                    self._post_silence_audio_frames += 1
                else:
                    self._post_silence_audio_frames = 0

                if self._post_silence_audio_frames >= self._silence_recovery_frames:
                    if not in_cooldown:
                        self._reset_song_state(
                            (
                                f"silencio de {self._silence_frames * 50}ms seguido de "
                                f"audio consistente ({self._post_silence_audio_frames} frames)"
                            )
                        )
                        self._silence_frames = 0
                        return
                    self._silence_triggered = False
                    self._post_silence_audio_frames = 0
            self._silence_frames = 0

            if not self._silence_triggered:
                self._post_silence_audio_frames = 0

        if is_silent or in_cooldown:
            return

        # ── Detecção por similaridade de espectro (janela deslizante) ────────
        current = self._normalize_spectrum(spectrum)
        if not current:
            return

        if self._change_baseline is None:
            self._change_baseline = list(current)
            self._change_window = []
            return

        sim = self._cosine_similarity(self._change_baseline, current)
        is_divergent = sim < self._change_similarity_threshold

        # Manter janela deslizante de tamanho fixo
        self._change_window.append(is_divergent)
        if len(self._change_window) > self._change_window_size:
            self._change_window.pop(0)

        # Disparar se mais de 60% dos frames na janela forem divergentes
        if len(self._change_window) == self._change_window_size:
            divergent_ratio = sum(self._change_window) / self._change_window_size
            if divergent_ratio >= 0.6:
                self._reset_song_state(
                    f"espectro divergente (sim={sim:.2f}, ratio={divergent_ratio:.0%}, janela={self._change_window_size}f)"
                )
                return

        # Atualizar baseline via EMA apenas em frames não-divergentes
        if not is_divergent:
            alpha = self._change_ema_alpha
            if 0.0 < alpha < 1.0:
                base = self._change_baseline
                for i in range(min(len(base), len(current))):
                    base[i] = (1.0 - alpha) * base[i] + alpha * current[i]
                self._change_baseline = self._normalize_spectrum(base)


    

    def _capture_duration_for_cycle(self) -> int:
        """Return capture duration for detection vs tracking cycles."""
        key = "tracking_capture_duration" if self._current_song_key is not None else "capture_duration"
        fallback = 5 if self._current_song_key is not None else 10
        return max(1, self._config.getint("Recognition", key, fallback=fallback))

    def _capture_fresh_audio(self) -> tuple[bytes, float]:
        """
        Captura um trecho FRESCO de áudio para uma nova tentativa de reconhecimento.
        
        Retorna: (audio_bytes, capture_start_time)
        """
        _LOG.debug("Capturando trecho fresco de áudio...")
        capture_start = time.perf_counter()
        duration = self._capture_duration_for_cycle()
        audio_data = self._audio.capture(duration)
        return audio_data, capture_start

    def _wait_interval(self) -> None:
        """Sleep for 'recognition_interval' seconds, checking the stop flag.

        Pode ser interrompido antecipadamente via _wake_event (ex: SMTC detectou troca).
        """
        interval = self._interval_for_cycle()
        # Usa dois waits encadeados: _wake_event acorda antes do timeout, _stop_flag para o loop
        self._wake_event.wait(timeout=interval)
        self._wake_event.clear()

    def _interval_for_cycle(self) -> float:
        """Return the appropriate interval based on current state."""
        if self._current_song_key is None:
            return self._config.getfloat("Recognition", "recognition_interval", fallback=5.0)
        else:
            return self._config.getfloat("Recognition", "tracking_interval", fallback=30.0)

    def _new_song_limit_reached(self) -> bool:
        if self._current_song_key is not None:
            return False
        now = time.monotonic()
        if now >= self._new_song_cooldown_until:
            return False
        remaining = int(self._new_song_cooldown_until - now)
        mins, secs = divmod(max(0, remaining), 60)
        self._emit('status_changed', f"⏳ Limite para nova música atingido — retomando em {mins}m{secs:02d}s")
        return True

    def _register_new_song_miss(self) -> None:
        if self._current_song_key is not None:
            return
        max_attempts = max(
            1,
            self._config.getint("Recognition", "new_song_max_attempts", fallback=12),
        )
        cooldown_s = max(
            0,
            self._config.getint("Recognition", "new_song_attempt_cooldown_s", fallback=120),
        )
        self._new_song_attempts += 1
        if self._new_song_attempts < max_attempts:
            return

        self._new_song_attempts = 0
        if cooldown_s <= 0:
            return

        self._new_song_cooldown_until = time.monotonic() + cooldown_s
        _LOG.warning(
            "Limite de tentativas para nova música atingido (%d). Pausando por %ds.",
            max_attempts,
            cooldown_s,
        )

    def _cycle(self) -> None:
        """Single recognition cycle."""
        start_time = time.perf_counter()

        if self._new_song_limit_reached():
            return

        if self._current_song_key is not None and self._disable_api_recognition_while_tracking:
            # Sem chamadas de rede em tracking. O detector local limpa _current_song_key ao detectar mudanca.
            return
        
        # Check rate limit
        now = time.perf_counter()
        if now < self._rate_limit_until:
            remaining = int(self._rate_limit_until - now)
            if remaining % 60 == 0:  # Log every minute
                _LOG.warning(f"Rate-limited. Retrying in {remaining // 60} minutes")
            return

        # Emitir status apenas quando procurando nova música — em tracking
        # a re-sincronização ocorre silenciosamente para não poluir a UI.
        if self._current_song_key is None:
            self._emit('status_changed', "🎵 Capturando áudio...")

        # Capture audio (pode demorar vários segundos - operação bloqueante)
        try:
            capture_start = time.perf_counter()
            duration = self._capture_duration_for_cycle()
            _LOG.debug(f"Worker: iniciando captura de {duration}s de áudio...")
            audio_data = self._audio.capture(duration)
            # Referência dinâmica: instante do último sample capturado menos a latência
            # de buffer do WASAPI (delay entre render → loopback disponível).
            # Isso faz a compensação de timecode se adaptar automaticamente ao hardware.
            _audio_end = self._audio.last_audio_end_time
            _wasapi_latency = self._audio.last_input_latency_s
            capture_end = _audio_end - _wasapi_latency  # ponto exato de "agora" no sinal de áudio
            if _wasapi_latency > 0:
                _LOG.debug(
                    "Timing dinâmico: last_read=%.3fs atrás, WASAPI latency=%.0fms → capture_ref=%.3fs atrás",
                    time.perf_counter() - _audio_end,
                    _wasapi_latency * 1000,
                    time.perf_counter() - capture_end,
                )
            _LOG.debug(f"Worker: captura concluída ({len(audio_data) if audio_data else 0} bytes)")
            if audio_data is None or len(audio_data) == 0:
                _LOG.warning("Nenhum áudio capturado após %ds — dispositivo de captura retornou vazio.", duration)
                return
        except Exception as exc:
            # Se foi interrompido por shutdown, não logar como erro
            if "shutdown" in str(exc).lower() or self._stop_flag.is_set():
                _LOG.debug(f"Captura de áudio interrompida: {exc}")
                return
            _LOG.error(f"Erro ao capturar áudio: {exc}", exc_info=True)
            self._emit('error_occurred', f"Erro ao capturar áudio: {exc}")
            return

        # Checar se foi solicitado stop durante a captura
        if self._stop_flag.is_set():
            _LOG.debug("Worker: stop solicitado após captura de áudio")
            return

        # Skip recognition if in tracking mode and not needed
        if self._should_skip_recognition():
            song_title = self._current_song_key[0] if self._current_song_key else "?"
            _LOG.debug("Rastreando '%s' — intervalo não atingido, pulando chamada de API.", song_title)
            return

        # Modo debug: salvar WAV e pular APIs
        if self._debug_only_flag.is_set():
            from src.song_recognition import _save_debug_audio
            _save_debug_audio(audio_data, provider_name="debug_only")
            self._emit('status_changed', "🔧 Áudio debug salvo em cache/debug_audio/")
            return

        # Recognize (pode demorar vários segundos - HTTP request bloqueante)
        self._emit('status_changed', "🔍 Reconhecendo música...")
        try:
            _LOG.debug("Worker: iniciando reconhecimento...")
            result, _ = self._recognizer.recognize(audio_data, capture_start)
            elapsed = time.perf_counter() - start_time
            found = result is not None
            _LOG.debug(f"Worker: reconhecimento concluído em {elapsed:.1f}s | encontrado={found}")
            
            if found and result:
                self._handle_song_found(result, capture_end)
            else:
                self._handle_song_not_found()
                
        except RateLimitError as exc:
            _LOG.error(f"Rate limit atingido: {exc}")
            self._rate_limit_until = time.perf_counter() + self._rate_limit_cooldown_s
            self._emit('error_occurred', "⚠️ Limite de API atingido. Aguardando...")
        except Exception as exc:
            _LOG.error(f"Erro no reconhecimento: {exc}", exc_info=True)
            self._emit('error_occurred', f"Erro: {exc}")

    def _should_skip_recognition(self) -> bool:
        """
        Se estamos rastreando uma música e ainda não passou tempo suficiente,
        pula o reconhecimento para economizar API calls.
        """
        if self._current_song_key is None:
            return False
        
        if self._current_song_duration_s <= 0:
            return False
        
        time_since_last = time.perf_counter() - self._last_recognition_time
        time_until_next = self._time_until_next_check()
        
        return time_since_last < time_until_next

    def _time_until_next_check(self) -> float:
        """Calcula quando fazer o próximo check baseado na duração da música e confiança."""
        if self._current_song_key is None:
            return 0.0
        
        # Se última confiança foi baixa, reconhecer mais frequentemente (a cada 2s)
        if self._last_confidence is not None and self._last_confidence < self._low_confidence_threshold:
            return self._min_recognition_interval_s
        
        if self._current_song_duration_s <= 0:
            # Sem duração, reconhecer a cada 15s
            return 15.0
        
        # Esperar até perto do fim da música
        # Mas reconhecer a cada ~15s no máximo (mais responsivo para detectar mudanças)
        tracking_interval = min(
            self._current_song_duration_s * 0.85,  # 85% da duração
            15.0  # Máximo 15s
        )
        return max(5.0, tracking_interval)  # Mínimo 5s

    def _check_confidence_for_song_change(self, result) -> bool:
        """Verifica se reconhecimento tem confiança baixa (pode ser mudança de música).
        
        Returns: True se deve ignorar reconhecimento (mudança suspeitada), False se OK.
        """
        self._last_confidence = result.confidence or 0.0
        
        # Se confiança for baixa, aumentar contador
        if result.confidence is not None and result.confidence < self._low_confidence_threshold:
            self._low_confidence_count += 1
            _LOG.debug(
                f"⚠️ Confiança baixa ({result.confidence:.1%}). "
                f"Contador: {self._low_confidence_count}/{self._low_confidence_trigger}"
            )
        else:
            # Reset se confiança melhorar
            if self._low_confidence_count > 0:
                _LOG.debug(f"✅ Confiança recuperada ({result.confidence:.1%}). Resetando contador.")
            self._low_confidence_count = 0
        
        # Se muitos reconhecimentos com confiança baixa, assumir mudança de música
        if self._low_confidence_count >= self._low_confidence_trigger:
            _LOG.warning(
                f"⚠️ {self._low_confidence_count} reconhecimentos com baixa confiança. "
                f"Assumindo mudança de música."
            )
            self._low_confidence_count = 0
            return True  # Ignore este resultado
        
        return False  # Aceitar resultado

    def _handle_song_found(self, result, capture_ref: float) -> None:
        """Processa resultado quando música é encontrada.
        
        `capture_ref` deve ser o instante em que a captura de áudio TERMINOU
        (time.perf_counter() logo após audio.capture() retornar).
        """
        song_key = (result.title, result.artist, result.album or "")
        self._last_recognition_time = time.perf_counter()
        self._current_song_duration_s = result.duration_s or 0.0
        
        # Verificar se confiança indica mudança de música
        if self._check_confidence_for_song_change(result):
            return  # Ignorar este reconhecimento
        
        # Emit song found
        self._emit('song_found', result.title, result.artist, result.album or "")
        
        # Check if song changed
        if song_key == self._current_song_key:
            # Same song, just update timecode
            if result.timecode_ms is not None and result.timecode_ms > 0:
                self._emit_compensated_timecode(
                    result.timecode_ms,
                    capture_ref,
                    "🎵 Timecode compensado",
                )
            self._miss_streak = 0
            self._new_song_attempts = 0
            self._new_song_cooldown_until = 0.0
            return
        
        # New song detected
        conf_str = f"{result.confidence:.1%}" if result.confidence is not None else "N/A"
        _LOG.info(f"Nova música: {result.title} - {result.artist} (confiança={conf_str})")
        self._current_song_key = song_key
        self._change_baseline = None
        self._change_bad_frames = 0
        self._miss_streak = 0
        self._new_song_attempts = 0
        self._new_song_cooldown_until = 0.0
        self._low_confidence_count = 0  # Reset quando muda de música com sucesso
        
        # Fetch and emit lyrics
        self._handle_lyrics_for_song(song_key, result, capture_ref)

    def _handle_lyrics_for_song(self, song_key, result, capture_start: float) -> None:
        """Busca e emite letras para a música (de forma assíncrona)."""
        _LOG.info(f"🎵 Buscando letras para: '{result.title}' - '{result.artist}' (album='{result.album}', dur={result.duration_s}s)")
        
        # Check cache
        if song_key in self._lyrics_cache:
            cached = self._lyrics_cache[song_key]
            if cached is None:
                self._emit('lyrics_not_found')
            else:
                content, synced, *_rest = cached
                provider = _rest[0] if _rest else ""
                self._emit('lyrics_ready', content, synced, capture_start, provider)
            
            # Update timecode anyway
            if result.timecode_ms is not None and result.timecode_ms > 0:
                self._emit_compensated_timecode(
                    result.timecode_ms,
                    capture_start,
                    "🎵 Timecode inicial compensado",
                )
            return
        
        # Cancelar busca anterior se ainda estiver rodando
        if self._pending_lyrics_future and not self._pending_lyrics_future.done():
            _LOG.debug("Cancelando busca de letras anterior que ainda estava rodando")
            self._pending_lyrics_future.cancel()
        
        # Fetch lyrics em thread separada (NÃO BLOQUEIA)
        self._emit('status_changed', "📝 Buscando letras...")
        self._emit('lyrics_loading')
        
        # Submeter busca para ThreadPool
        self._pending_lyrics_future = self._lyrics_executor.submit(
            self._fetch_lyrics_async,
            song_key,
            result,
            capture_start
        )
        
        # Callback quando completar (não bloqueia)
        self._pending_lyrics_future.add_done_callback(
            lambda f: self._on_lyrics_fetched(f, song_key, result, capture_start)
        )

    def _fetch_lyrics_async(self, song_key, result, capture_start: float) -> tuple:
        """Busca letras (roda em thread separada) usando os metadados diretos
        retornados pela API de reconhecimento de música."""
        try:
            title = result.title
            artist = result.artist

            # Não usar o álbum se ele for idêntico ao título: é um artefato
            # de algumas APIs (ex: ACRCloud) que preenchem o campo album com o
            # nome da faixa quando não conhecem o álbum real.
            album = result.album
            if album.lower() == title.lower():
                _LOG.debug(
                    "Album='%s' é igual ao título — omitindo da busca de letras.",
                    album,
                )
                album = ""

            _LOG.info("🔍 Buscando letras via lrclib: title='%s' artist='%s' album='%s'", title, artist, album or '(omitido)')
            lyrics_result = self._lyrics.fetch(
                title=title,
                artist=artist,
                album=album,
                duration_s=result.duration_s,
            )
            _LOG.info("✅ Fetch concluído: resultado=%s", 'encontrado' if lyrics_result and lyrics_result.lines else 'não encontrado')
            return (lyrics_result, None)
        except Exception as exc:
            _LOG.error("❌ Erro ao buscar letras: %s", exc, exc_info=True)
            return (None, exc)

    def _on_lyrics_fetched(self, future: Future, song_key, result, capture_start: float) -> None:
        """Callback quando busca de letras completa."""
        if future.cancelled():
            _LOG.debug("Busca de letras foi cancelada")
            return
        
        try:
            lyrics_result, error = future.result(timeout=0.1)
            
            if error:
                _LOG.error(f"Erro ao buscar letras: {error}", exc_info=True)
                self._lyrics_cache[song_key] = None
                self._emit('lyrics_not_found')
                return
            
            if lyrics_result and lyrics_result.lines:
                # Converter para formato de texto
                if lyrics_result.synced and lyrics_result.raw_lrc:
                    lyrics_text = lyrics_result.raw_lrc
                else:
                    lyrics_text = "\n".join(lyrics_result.lines)
                
                self._lyrics_cache[song_key] = (lyrics_text, lyrics_result.synced, lyrics_result.provider)

                # Emitir timecode ANTES das letras: Flutter recebe a posição
                # correta antes de renderizar a primeira linha, evitando o
                # flash em posição 0 durante os primeiros frames.
                if result.timecode_ms is not None and result.timecode_ms > 0:
                    self._emit_compensated_timecode(
                        result.timecode_ms,
                        capture_start,  # capture_end herdado pelo closure
                        "🎵 Timecode antes de lyrics_ready",
                    )

                self._emit('lyrics_ready', lyrics_text, lyrics_result.synced, capture_start, lyrics_result.provider)

                # 🎤 STT Sync: Atualizar letras e iniciar thread
                self._update_stt_lyrics(lyrics_text)
                self._start_stt_loop()
            else:
                self._lyrics_cache[song_key] = None
                self._emit('lyrics_not_found')
        except Exception as exc:
            _LOG.error(f"Erro ao processar resultado de busca de letras: {exc}", exc_info=True)
            self._lyrics_cache[song_key] = None
            self._emit('lyrics_not_found')

    def _handle_song_not_found(self) -> None:
        """Processa resultado quando música não é encontrada."""
        self._register_new_song_miss()
        self._miss_streak += 1
        self._last_confidence = None  # Reset confiança
        
        if self._miss_streak >= self._miss_reset_threshold:
            _LOG.warning(
                f"🚫 Falha em {self._miss_streak} reconhecimentos consecutivos. "
                f"Resetando música atual e força redetecção."
            )
            self._current_song_key = None
            self._current_song_duration_s = 0.0
            self._miss_streak = 0
            self._low_confidence_count = 0  # Reset contador de confiança também
            self._emit('song_not_found')
        
        self._emit('status_changed', 'Aguardando música...')

    # ─ STT Sync Integration (Fase 2) ──────────────────────────────────────

    def _init_stt(self) -> None:
        """Inicializa SpeechRecognizer e LyricsMatcher (lazy loading)."""
        if not _STT_AVAILABLE or not self._stt_enabled:
            return
        
        try:
            model_size = self._config.get("SpeechSync", "model_size", fallback="tiny")
            device = self._config.get("SpeechSync", "device", fallback="cuda")
            min_similarity = self._config.getfloat("SpeechSync", "min_similarity", fallback=0.65)
            
            _LOG.info(f"🎤 STT Sync: Inicializando com model_size={model_size}, device={device}")
            
            self._stt_recognizer = SpeechRecognizer(model_size=model_size, device=device)
            self._stt_matcher = LyricsMatcher(min_similarity=min_similarity)
            
            _LOG.info("🎤 STT Sync: Inicializado com sucesso")
        except Exception as e:
            # Para erros de DLL (OSError no Windows) o traceback é longo mas sem
            # informação acionável — logar apenas a mensagem e desativar STT.
            is_dll_error = isinstance(e, OSError) and "WinError" in str(e)
            _LOG.error(
                "❌ Erro ao inicializar STT (%s): %s%s",
                type(e).__name__,
                e,
                "" if is_dll_error else " — verifique instalação do faster-whisper/torch",
                exc_info=not is_dll_error,
            )
            if is_dll_error:
                _LOG.warning(
                    "Dica: o PyTorch instalado parece incompatível com este ambiente. "
                    "Tente: pip install torch --index-url https://download.pytorch.org/whl/cpu"
                )
            self._stt_enabled = False

    def _update_stt_lyrics(self, lyrics_text: str) -> None:
        """Atualiza letras no matcher STT quando nova música é encontrada."""
        if not self._stt_enabled or not self._stt_matcher:
            return
        
        try:
            # Parse lyrics_text em linhas
            lines = [line.strip() for line in lyrics_text.split('\n') if line.strip()]
            
            with self._stt_lock:
                self._current_lyrics_for_stt = lines
                self._current_lyrics_index = 0
            
            self._stt_matcher.set_lyrics(lines)
            _LOG.info("🎤 STT: Letras carregadas no matcher (%d linhas).", len(lines))
        except Exception as e:
            _LOG.error(f"❌ Erro ao atualizar lyrics STT: {e}")

    def _start_stt_loop(self) -> None:
        """Inicia thread de STT se não estiver rodando (chamado após letra carregada)."""
        # A thread já foi iniciada em run() — este método é no-op após a migração lazy.
        pass

    def _stt_init_and_loop(self) -> None:
        """Carrega o modelo Whisper e entra no loop STT (roda inteiramente na thread STT)."""
        if not _STT_AVAILABLE or not self._stt_enabled:
            return
        # Inicializar modelo aqui — nunca no event loop do asyncio
        self._init_stt()
        if not self._stt_recognizer:
            _LOG.error("❌ STT desativado: falha ao carregar modelo Whisper.")
            return
        self._stt_running = True
        self._stt_loop()

    def _stop_stt_loop(self) -> None:
        """Para thread de STT."""
        self._stt_running = False
        if self._stt_thread and self._stt_thread.is_alive():
            self._stt_thread.join(timeout=2)

    def _stt_loop(self) -> None:
        """Loop principal de processamento STT (roda em thread separada)."""
        chunk_duration = self._config.getfloat("SpeechSync", "chunk_duration_s", fallback=2.5)
        _waiting_logged = False

        while self._stt_running and not self._stop_flag.is_set():
            if self._pause_flag.is_set() or not self._current_lyrics_for_stt:
                if not _waiting_logged:
                    _LOG.info("🎤 STT aguardando letras da música atual...")
                    _waiting_logged = True
                time.sleep(0.1)
                continue

            _waiting_logged = False  # reset para logar novamente se perder as letras
            
            try:
                # Capturar chunk de áudio
                audio_chunk = self._audio.capture_chunk(duration_s=chunk_duration)
                
                if audio_chunk is None or len(audio_chunk) == 0:
                    time.sleep(0.1)
                    continue
                
                # Reconhecer voz no chunk
                segment = self._stt_recognizer.recognize_chunk(audio_chunk, sample_rate=44100)
                
                if not segment:
                    continue
                
                # Emit reconhecimento para debug
                _LOG.info("🎤 STT reconheceu: '%s' (confiança=%.2f)", segment.text, segment.confidence)
                self._emit('stt_recognized', segment.text, segment.confidence)
                
                # Encontrar match na letra
                with self._stt_lock:
                    current_index = self._current_lyrics_index
                    lyrics = self._current_lyrics_for_stt
                
                if not lyrics:
                    continue
                
                match = self._stt_matcher.find_best_match(segment.text, current_index=current_index)
                
                if match and match.similarity > 0.65:
                    # Atualizar índice se modo não é timestamp_only
                    if self._stt_mode in ("stt_only", "hybrid"):
                        with self._stt_lock:
                            old_index = self._current_lyrics_index
                            self._current_lyrics_index = match.line_index
                        
                        if old_index != match.line_index:
                            _LOG.info("🎤 STT: sincronizou linha %d → %d (similaridade=%.0f%%).", old_index, match.line_index, match.similarity * 100)
                            self._emit('stt_matched', match.line_index, match.similarity)
                    
                    self._stt_last_segment = segment
            
            except Exception as e:
                _LOG.error("❌ Erro em STT loop: %s", e, exc_info=True)
                time.sleep(1)
