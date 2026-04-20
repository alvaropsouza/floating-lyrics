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

# STT Sync (Fase 2) - Importação condicional
try:
    from src.speech_recognition import SpeechRecognizer
    from src.lyrics_matcher import LyricsMatcher
    _STT_AVAILABLE = True
except ImportError:
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
        self._change_similarity_threshold = self._config.getfloat(
            "Recognition",
            "local_change_similarity_threshold",
            fallback=0.72,
        )
        self._change_frames_threshold = max(
            1,
            self._config.getint("Recognition", "local_change_frames_threshold", fallback=30),
        )
        self._change_min_energy = self._config.getfloat(
            "Recognition",
            "local_change_min_energy",
            fallback=0.08,
        )
        self._change_ema_alpha = self._config.getfloat(
            "Recognition",
            "local_change_ema_alpha",
            fallback=0.05,
        )
        self._change_cooldown_s = max(
            0,
            self._config.getint("Recognition", "local_change_cooldown_s", fallback=5),
        )
        # Janela deslizante de frames: bool (True = frame divergente)
        self._change_window_size = self._change_frames_threshold
        self._change_window: list[bool] = []
        self._change_baseline: list[float] | None = None
        self._last_change_trigger_at = 0.0

        # Detecção silence→audio (gatilho mais confiável que espectro)
        self._silence_frames = 0
        self._silence_trigger_frames = max(
            1,
            self._config.getint("Recognition", "silence_trigger_frames", fallback=20),
        )  # 20 frames * 50ms = 1s de silêncio para armar o gatilho
        self._silence_triggered = False  # True = estava em silêncio, esperando nova música


        
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
        
        # Ler configurações STT de config.ini
        if _STT_AVAILABLE:
            self._stt_enabled = self._config.getboolean("SpeechSync", "enabled", fallback=False)
            self._stt_mode = self._config.get("SpeechSync", "mode", fallback="timestamp_only")
            self._init_stt()
        
        # Callbacks para STT (adicionados aos callbacks existentes)
        self._callbacks.setdefault('stt_recognized', [])
        self._callbacks.setdefault('stt_matched', [])
        self._callbacks.setdefault('sync_corrected', [])

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
        self._pause_flag.clear()
        
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
        """Loop contínuo de captura de espectro de áudio."""
        _LOG.info("🎵 Thread de captura de espectro iniciada")
        error_count = 0
        max_errors = 10
        first_call = True
        
        while self._spectrum_running and not self._stop_flag.is_set():
            if self._pause_flag.is_set():
                # Zera visualizador durante pausa para reforçar feedback na UI.
                self._emit('audio_spectrum', [0.0] * 32)
                self._stop_flag.wait(timeout=0.2)
                continue

            try:
                if first_call:
                    _LOG.info("Iniciando primeira captura de espectro...")
                    first_call = False
                    
                # Capturar espectro (100ms de áudio)
                spectrum = self._audio.capture_spectrum(duration_ms=100, num_bars=32)
                self._emit('audio_spectrum', spectrum)
                error_count = 0  # Reset contador de erros em caso de sucesso
                
                # Detectar silêncio prolongado
                self._update_local_change_detector(spectrum)
                    
            except Exception as exc:
                error_count += 1
                if error_count <= 3:  # Logar apenas primeiros 3 erros
                    _LOG.warning(f"Erro ao capturar espectro ({error_count}/{max_errors}): {exc}")
                elif error_count == max_errors:
                    _LOG.error("Muitos erros consecutivos ao capturar espectro. Parando thread.")
                    break
            
            # Verificar stop flag antes de dormir
            if self._stop_flag.is_set():
                break
            
            # Atualizar ~20 vezes por segundo (50ms de sleep)
            time.sleep(0.05)
        
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
        self._emit('song_not_found')

    def _update_local_change_detector(self, spectrum: list[float]) -> None:
        if self._current_song_key is None:
            # Sem música ativa: manter apenas detecção de silêncio para não disparar desnecessariamente
            self._change_baseline = None
            self._change_window = []
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
            if self._silence_frames >= self._silence_trigger_frames:
                self._silence_triggered = True  # armar o gatilho
        else:
            if self._silence_triggered and not in_cooldown:
                # Havia silêncio prolongado e agora voltou o áudio = troca de faixa
                self._reset_song_state(
                    f"silencio de {self._silence_frames * 50}ms seguido de audio"
                )
                self._silence_frames = 0
                return
            self._silence_frames = 0
            self._silence_triggered = False

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
        """Sleep for 'recognition_interval' seconds, checking the stop flag."""
        interval = self._interval_for_cycle()
        self._stop_flag.wait(timeout=interval)

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

        # Emit status
        self._emit('status_changed', "🎵 Capturando áudio...")

        # Capture audio (pode demorar vários segundos - operação bloqueante)
        try:
            capture_start = time.perf_counter()
            duration = self._capture_duration_for_cycle()
            _LOG.debug(f"Worker: iniciando captura de {duration}s de áudio...")
            audio_data = self._audio.capture(duration)
            _LOG.debug(f"Worker: captura concluída ({len(audio_data) if audio_data else 0} bytes)")
            if audio_data is None or len(audio_data) == 0:
                _LOG.debug("No audio captured")
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
            _LOG.debug("Skipping recognition (tracking mode)")
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
            result, updated_capture_start = self._recognizer.recognize(audio_data, capture_start)
            elapsed = time.perf_counter() - start_time
            found = result is not None
            _LOG.debug(f"Worker: reconhecimento concluído em {elapsed:.1f}s | encontrado={found}")
            
            if found and result:
                self._handle_song_found(result, updated_capture_start, audio_data)
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

    def _handle_song_found(self, result, capture_start: float, audio_bytes: bytes) -> None:
        """Processa resultado quando música é encontrada."""
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
                # 🔧 COMPENSAÇÃO AUTOMÁTICA DE LATÊNCIA:
                # Adiciona o tempo decorrido desde a captura para compensar
                # o delay de: tempo de captura + processamento API + network
                elapsed_ms = int((time.perf_counter() - capture_start) * 1000)
                compensated_timecode = result.timecode_ms + elapsed_ms
                
                _LOG.debug(
                    f"🎵 Timecode compensado: {result.timecode_ms}ms + {elapsed_ms}ms delay = {compensated_timecode}ms"
                )
                
                self._emit('timecode_updated', compensated_timecode, capture_start)
            self._miss_streak = 0
            self._new_song_attempts = 0
            self._new_song_cooldown_until = 0.0
            return
        
        # New song detected
        _LOG.info(f"Nova música: {result.title} - {result.artist} (confiança={result.confidence:.1%})")
        self._current_song_key = song_key
        self._change_baseline = None
        self._change_bad_frames = 0
        self._miss_streak = 0
        self._new_song_attempts = 0
        self._new_song_cooldown_until = 0.0
        self._low_confidence_count = 0  # Reset quando muda de música com sucesso
        
        # Fetch and emit lyrics
        self._handle_lyrics_for_song(song_key, result, capture_start)

    def _handle_lyrics_for_song(self, song_key, result, capture_start: float) -> None:
        """Busca e emite letras para a música (de forma assíncrona)."""
        _LOG.info(f"🎵 Buscando letras para: '{result.title}' - '{result.artist}' (album='{result.album}', dur={result.duration_s}s)")
        
        # Check cache
        if song_key in self._lyrics_cache:
            cached = self._lyrics_cache[song_key]
            if cached is None:
                self._emit('lyrics_not_found')
            else:
                content, synced = cached
                self._emit('lyrics_ready', content, synced, capture_start)
            
            # Update timecode anyway
            if result.timecode_ms is not None and result.timecode_ms > 0:
                # 🔧 COMPENSAÇÃO AUTOMÁTICA DE LATÊNCIA
                elapsed_ms = int((time.perf_counter() - capture_start) * 1000)
                compensated_timecode = result.timecode_ms + elapsed_ms
                
                _LOG.debug(
                    f"🎵 Timecode inicial compensado: {result.timecode_ms}ms + {elapsed_ms}ms = {compensated_timecode}ms"
                )
                
                self._emit('timecode_updated', compensated_timecode, capture_start)
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
        """Busca letras (roda em thread separada).

        Se /search estiver disponível, usa para corrigir metadados (artista/título/álbum)
        antes de buscar letras no lrclib com dados mais precisos.
        """
        try:
            title = result.title
            artist = result.artist
            album = result.album

            # ── Buscar letras via lrclib com dados originais do reconhecimento
            _LOG.info("🔍 Buscando letras via lrclib: title='%s' artist='%s'", title, artist)
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
                
                self._lyrics_cache[song_key] = (lyrics_text, lyrics_result.synced)
                self._emit('lyrics_ready', lyrics_text, lyrics_result.synced, capture_start)
                
                # 🎤 STT Sync: Atualizar letras e iniciar thread
                self._update_stt_lyrics(lyrics_text)
                self._start_stt_loop()
                
                # Update timecode
                if result.timecode_ms is not None and result.timecode_ms > 0:
                    self._emit('timecode_updated', result.timecode_ms, capture_start)
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
            _LOG.error(f"❌ Erro ao inicializar STT: {e}", exc_info=True)
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
            _LOG.debug(f"🎤 STT: Letras atualizadas ({len(lines)} linhas)")
        except Exception as e:
            _LOG.error(f"❌ Erro ao atualizar lyrics STT: {e}")

    def _start_stt_loop(self) -> None:
        """Inicia thread de STT se não estiver rodando."""
        if not self._stt_enabled or not self._stt_recognizer:
            return
        
        if not self._stt_running:
            self._stt_running = True
            self._stt_thread = threading.Thread(
                target=self._stt_loop,
                daemon=True,
                name="STTSync"
            )
            self._stt_thread.start()
            _LOG.info("🎤 STT thread iniciada")

    def _stop_stt_loop(self) -> None:
        """Para thread de STT."""
        self._stt_running = False
        if self._stt_thread and self._stt_thread.is_alive():
            self._stt_thread.join(timeout=2)

    def _stt_loop(self) -> None:
        """Loop principal de processamento STT (roda em thread separada)."""
        chunk_duration = self._config.getfloat("SpeechSync", "chunk_duration_s", fallback=2.5)
        
        while self._stt_running and not self._stop_flag.is_set():
            if self._pause_flag.is_set() or not self._current_lyrics_for_stt:
                time.sleep(0.1)
                continue
            
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
                            _LOG.debug(f"🎤 STT: Linha {old_index} → {match.line_index} (similarity: {match.similarity:.2%})")
                            self._emit('stt_matched', match.line_index, match.similarity)
                    
                    self._stt_last_segment = segment
            
            except Exception as e:
                _LOG.error(f"❌ Erro em STT loop: {e}", exc_info=True)
                time.sleep(1)
