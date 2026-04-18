"""
Background worker headless (sem PyQt6) para usar com WebSocket.

Roda em thread separada e usa callbacks ao invés de Qt signals.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any

from src.song_recognition import RateLimitError


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
        self._current_song_key: tuple[str, str, str] | None = None
        self._miss_streak: int = 0
        self._lyrics_cache: dict[tuple[str, str, str], tuple[str, bool] | None] = {}
        self._rate_limit_until: float = 0.0
        self._rate_limit_cooldown_s: float = 30 * 60  # 30 minutes
        self._last_recognition_time: float = 0.0
        self._current_song_duration_s: float = 0.0

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
        
        # Detecção de silêncio para forçar redetecção
        self._low_spectrum_count = 0  # Contador de frames com espectro baixo
        # Configurar via config.ini ou usar padrões
        self._silence_frames_threshold = self._config.getint(
            "Recognition", "silence_frames_threshold", fallback=60
        )  # ~3s de silêncio (60 frames * 50ms)
        self._low_spectrum_max = self._config.getfloat(
            "Recognition", "silence_spectrum_threshold", fallback=0.05
        )  # Threshold: max 5% de energia normalizada
        
        # Histórico de espectro para detectar mudanças bruscas
        self._spectrum_history: list[float] = []  # Histórico de energias (últimos 20 frames = 1s)
        self._max_spectrum_history = 20
        self._spectrum_change_threshold = 0.4  # Mudança de 40% de energia = mudança de música
        
        # Detecção de confiança baixa
        self._last_confidence: float | None = None
        self._low_confidence_count = 0  # Contador de reconhecimentos com confiança baixa
        self._low_confidence_threshold = 0.5  # Reconhecimentos com <50% confidence
        self._low_confidence_trigger = 2  # Se 2 em seguida com confiança baixa = mudança
        
        # Rastreamento de mudanças rápidas
        self._min_recognition_interval_s = 2.0  # Se confidence for baixa, reconhecer novamente em 2s
        self._last_full_recognition_time: float = 0.0
        
        # Configurar callback de captura fresca no recognizer
        self._recognizer.set_fresh_capture_callback(self._capture_fresh_audio)

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
        
        # Cancelar busca de letras pendente e aguardar shutdown
        if self._pending_lyrics_future and not self._pending_lyrics_future.done():
            self._pending_lyrics_future.cancel()
        self._lyrics_executor.shutdown(wait=False, cancel_futures=True)

    def join(self, timeout=None) -> None:
        """Aguarda a thread terminar."""
        if self._spectrum_thread and self._spectrum_thread.is_alive():
            self._spectrum_thread.join(timeout=2)
        super().join(timeout)

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
            self._cycle()
            if self._stop_flag.is_set():
                break
            self._wait_interval()
        
        # Parar thread de espectro
        self._spectrum_running = False
        _LOG.info("Worker headless parado")

    def _spectrum_loop(self) -> None:
        """Loop contínuo de captura de espectro de áudio."""
        _LOG.info("🎵 Thread de captura de espectro iniciada")
        error_count = 0
        max_errors = 10
        first_call = True
        
        while self._spectrum_running:
            try:
                if first_call:
                    _LOG.info("Iniciando primeira captura de espectro...")
                    first_call = False
                    
                # Capturar espectro (100ms de áudio)
                spectrum = self._audio.capture_spectrum(duration_ms=100, num_bars=32)
                self._emit('audio_spectrum', spectrum)
                error_count = 0  # Reset contador de erros em caso de sucesso
                
                # Detectar silêncio prolongado
                self._check_silence_and_reset(spectrum)
                    
            except Exception as exc:
                error_count += 1
                if error_count <= 3:  # Logar apenas primeiros 3 erros
                    _LOG.warning(f"Erro ao capturar espectro ({error_count}/{max_errors}): {exc}")
                elif error_count == max_errors:
                    _LOG.error("Muitos erros consecutivos ao capturar espectro. Parando thread.")
                    break
            
            # Atualizar ~20 vezes por segundo (50ms de sleep)
            time.sleep(0.05)
        
        _LOG.info("Thread de espectro parada")

    def _check_silence_and_reset(self, spectrum: list[float]) -> None:
        """Detecta silêncio prolongado, mudanças bruscas de espectro e força redetecção se necessário."""
        # Ignorar se não há música tocando atualmente
        if self._current_song_key is None:
            self._low_spectrum_count = 0
            self._spectrum_history.clear()
            return
        
        # Calcular energia média do espectro (normalizado 0-1)
        avg_energy = sum(spectrum) / len(spectrum) if spectrum else 0.0
        
        # ═════════════════════════════════════════════════════════════════
        # 1. Detecção de mudança bruta de espectro (crossfade ou mudança rápida)
        # ═════════════════════════════════════════════════════════════════
        if self._spectrum_history:
            prev_energy = self._spectrum_history[-1]
            energy_change = abs(avg_energy - prev_energy) / (prev_energy + 0.001)  # Evitar divisão por zero
            
            # Se energia mudou drasticamente (ex: 0.8 → 0.3), pode ser mudança de música
            if energy_change > self._spectrum_change_threshold and avg_energy < 0.3:
                _LOG.warning(
                    f"⚠️ Mudança brusca de espectro detectada (Δ={energy_change:.1%}). "
                    f"Resetando música e forçando redetecção rápida."
                )
                self._current_song_key = None
                self._current_song_duration_s = 0.0
                self._miss_streak = 0
                self._low_spectrum_count = 0
                self._spectrum_history.clear()
                self._last_confidence = None
                self._low_confidence_count = 0
                self._emit('song_not_found')
                return
        
        # Manter histórico de últimas energias
        self._spectrum_history.append(avg_energy)
        if len(self._spectrum_history) > self._max_spectrum_history:
            self._spectrum_history.pop(0)
        
        # ═════════════════════════════════════════════════════════════════
        # 2. Detecção de silêncio prolongado
        # ═════════════════════════════════════════════════════════════════
        # Checar se está abaixo do threshold de silêncio
        if avg_energy < self._low_spectrum_max:
            self._low_spectrum_count += 1
            
            # Se atingiu threshold de silêncio, resetar música e forçar redetecção
            if self._low_spectrum_count >= self._silence_frames_threshold:
                _LOG.info(
                    f"🔇 Silêncio detectado por {self._low_spectrum_count} frames (~{self._low_spectrum_count * 0.05:.1f}s). "
                    f"Resetando música atual e forçando redetecção."
                )
                
                # Resetar estado da música
                self._current_song_key = None
                self._current_song_duration_s = 0.0
                self._miss_streak = 0
                self._low_spectrum_count = 0
                self._spectrum_history.clear()
                self._last_confidence = None
                self._low_confidence_count = 0
                
                # Emitir evento de música não encontrada para limpar UI
                self._emit('song_not_found')
                
                _LOG.debug("Estado resetado. Próximo ciclo tentará detectar nova música.")
        else:
            # Resetar contador se voltou a ter áudio
            if self._low_spectrum_count > 0:
                self._low_spectrum_count = 0

    # ── Private helpers ─────────────────────────────────────────────────────

    def _capture_fresh_audio(self) -> tuple[bytes, float]:
        """
        Captura um trecho FRESCO de áudio para uma nova tentativa de reconhecimento.
        
        Retorna: (audio_bytes, capture_start_time)
        """
        _LOG.debug("Capturando trecho fresco de áudio...")
        capture_start = time.perf_counter()
        duration = self._config.getint("Capture", "capture_duration", fallback=10)
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

    def _cycle(self) -> None:
        """Single recognition cycle."""
        start_time = time.perf_counter()
        
        # Check rate limit
        now = time.perf_counter()
        if now < self._rate_limit_until:
            remaining = int(self._rate_limit_until - now)
            if remaining % 60 == 0:  # Log every minute
                _LOG.warning(f"Rate-limited. Retrying in {remaining // 60} minutes")
            return

        # Emit status
        self._emit('status_changed', "🎵 Capturando áudio...")

        # Capture audio
        try:
            capture_start = time.perf_counter()
            duration = self._config.getint("Capture", "capture_duration", fallback=10)
            audio_data = self._audio.capture(duration)
            if audio_data is None or len(audio_data) == 0:
                _LOG.debug("No audio captured")
                return
        except Exception as exc:
            _LOG.error(f"Erro ao capturar áudio: {exc}", exc_info=True)
            self._emit('error_occurred', f"Erro ao capturar áudio: {exc}")
            return

        # Skip recognition if in tracking mode and not needed
        if self._should_skip_recognition():
            _LOG.debug("Skipping recognition (tracking mode)")
            return

        # Recognize
        self._emit('status_changed', "🔍 Reconhecendo música...")
        try:
            result, updated_capture_start = self._recognizer.recognize(audio_data, capture_start)
            elapsed = time.perf_counter() - start_time
            found = result is not None
            _LOG.debug(f"{elapsed:.1f}s | encontrado={found}")
            
            if found and result:
                self._handle_song_found(result, updated_capture_start)
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

    def _handle_song_found(self, result, capture_start: float) -> None:
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
            return
        
        # New song detected
        _LOG.info(f"Nova música: {result.title} - {result.artist} (confiança={result.confidence:.1%})")
        self._current_song_key = song_key
        self._miss_streak = 0
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
        """Busca letras (roda em thread separada)."""
        try:
            _LOG.info(f"🔍 Iniciando fetch de letras: title='{result.title}' artist='{result.artist}'")
            lyrics_result = self._lyrics.fetch(
                title=result.title,
                artist=result.artist,
                album=result.album,
                duration_s=result.duration_s,
            )
            _LOG.info(f"✅ Fetch concluído: resultado={'encontrado' if lyrics_result and lyrics_result.lines else 'não encontrado'}")
            return (lyrics_result, None)
        except Exception as exc:
            _LOG.error(f"❌ Erro ao buscar letras: {exc}", exc_info=True)
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
            self._spectrum_history.clear()  # Limpar histórico de espectro
            self._emit('song_not_found')
        
        self._emit('status_changed', 'Música não identificada')
