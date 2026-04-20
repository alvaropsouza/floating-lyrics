# Sincronização de Letras por Reconhecimento de Voz (Speech-to-Text)

## 📋 Visão Geral

Implementação de sincronização inteligente de letras usando reconhecimento de voz em tempo real, que detecta o que está sendo cantado e sincroniza automaticamente com a letra carregada.

## 🎯 Objetivos

### Funcionalidades
- ✅ Reconhecer voz cantada em tempo real via WASAPI loopback
- ✅ Comparar texto reconhecido com letra conhecida (fuzzy matching)
- ✅ Atualizar posição da letra dinamicamente
- ✅ Manter sincronização mesmo sem timestamps LRC
- ✅ Auto-correção de drift (dessincronização)
- ✅ Fallback inteligente para sincronização por tempo

### Requisitos Técnicos
- Latência: **< 1 segundo** (idealmente 400-700ms)
- Precisão: **> 75%** de palavras reconhecidas corretamente
- Performance: Não bloquear UI, processamento em thread separada
- Compatibilidade: Windows 10/11, GPU opcional mas recomendada
- Offline: Funcionar sem internet (usando faster-whisper)

---

## 🏗️ Arquitetura

### Arquitetura Híbrida (Recomendada)

```
┌─────────────────────────────────────────────────────────────────┐
│                     PIPELINE DE SINCRONIZAÇÃO                    │
└─────────────────────────────────────────────────────────────────┘

   CAMADA 1: IDENTIFICAÇÃO (Já existe)
   ┌────────────────────────────────────┐
   │  Audio Fingerprinting              │
   │  - AudD / ACRCloud                 │
   │  - Identifica música + timecode    │
   │  - Latência: 2-5s                  │
   │  - Precisão: 99%                   │
   └──────────────┬─────────────────────┘
                  │
                  ▼
   ┌────────────────────────────────────┐
   │  Lyrics Fetcher                    │
   │  - lrclib / Musixmatch             │
   │  - Carrega letra completa          │
   │  - Com/sem timestamps LRC          │
   └──────────────┬─────────────────────┘
                  │
                  ▼
   CAMADA 2: SINCRONIZAÇÃO (NOVA - 3 modos)
   ┌────────────────────────────────────┐
   │ 🅰️ Modo Timestamp (atual)          │
   │  - Interpolação por tempo          │
   │  - 20 FPS (50ms updates)           │
   │  - Quando: LRC com timestamps      │
   │  - Precisão: Boa (drift 1-2s/min)  │
   └────────────────────────────────────┘
   
   ┌────────────────────────────────────┐
   │ 🅱️ Modo STT Puro (NOVO)            │
   │  - faster-whisper tiny/base        │
   │  - Chunks de 2-3s                  │
   │  - Fuzzy match com letra           │
   │  - Quando: Sem timestamps LRC      │
   │  - Precisão: Média (75-85%)        │
   │  - Latência: 400-700ms             │
   └────────────────────────────────────┘
   
   ┌────────────────────────────────────┐
   │ 🅲 Modo Híbrido (RECOMENDADO)      │
   │  1. Timestamp dá posição base      │
   │  2. STT valida linha atual         │
   │  3. Auto-correção se divergir      │
   │  - Quando: LRC disponível + GPU    │
   │  - Precisão: Excelente (95%+)      │
   │  - Latência: 50ms (timestamp)      │
   │              + 500ms (validação)   │
   └────────────────────────────────────┘

                  │
                  ▼
   ┌────────────────────────────────────┐
   │  UI Update (Flutter)               │
   │  - Scroll suave para linha atual   │
   │  - Highlight da linha ativa        │
   └────────────────────────────────────┘
```

---

## 📦 Componentes Novos

### 1. `src/speech_recognition.py`

**Responsabilidade:** Encapsular reconhecimento de voz (STT)

```python
"""
Speech-to-Text para sincronização de letras musicais.
Usa faster-whisper para reconhecimento offline de voz cantada.
"""

from faster_whisper import WhisperModel
import numpy as np
from typing import Optional, List
from dataclasses import dataclass

@dataclass
class SpeechSegment:
    """Segmento de texto reconhecido."""
    text: str
    confidence: float
    start_ms: int
    end_ms: int

class SpeechRecognizer:
    """
    Reconhecedor de voz otimizado para música.
    
    Usa faster-whisper com configurações otimizadas para:
    - Baixa latência (modelo tiny/base)
    - Voz cantada (não apenas fala)
    - Português brasileiro
    """
    
    def __init__(self, model_size: str = "tiny", device: str = "cuda"):
        """
        Args:
            model_size: "tiny", "base", "small", "medium", "large"
                - tiny: ~40MB, 300-500ms, precisão ~75%
                - base: ~75MB, 500-800ms, precisão ~85%
            device: "cuda" (GPU) ou "cpu"
        """
        self.model = WhisperModel(
            model_size, 
            device=device,
            compute_type="int8" if device == "cuda" else "int8"
        )
        self.sample_rate = 16000  # Whisper requer 16kHz
        
    def recognize_chunk(
        self, 
        audio_data: bytes, 
        duration_s: float = 3.0
    ) -> Optional[SpeechSegment]:
        """
        Reconhece voz em um chunk de áudio.
        
        Args:
            audio_data: Áudio PCM (16-bit, mono)
            duration_s: Duração do chunk (2-3s recomendado)
            
        Returns:
            SpeechSegment com texto reconhecido ou None se silêncio
        """
        # Converter bytes → numpy array
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_float = audio_array.astype(np.float32) / 32768.0
        
        # Resample para 16kHz se necessário
        # (TODO: implementar resampling se audio vier em 44.1kHz)
        
        segments, info = self.model.transcribe(
            audio_float,
            language="pt",
            beam_size=1,  # Beam size 1 = mais rápido
            best_of=1,
            temperature=0.0,
            vad_filter=True,  # Filtro de detecção de voz
            vad_parameters=dict(
                threshold=0.5,
                min_speech_duration_ms=250,
                min_silence_duration_ms=100
            )
        )
        
        # Extrair primeiro segmento
        for segment in segments:
            return SpeechSegment(
                text=segment.text.strip(),
                confidence=segment.avg_logprob,  # -0.5 a 0.0 (0 = perfeito)
                start_ms=int(segment.start * 1000),
                end_ms=int(segment.end * 1000)
            )
        
        return None
    
    def recognize_stream(
        self, 
        audio_generator, 
        chunk_duration_s: float = 2.5
    ):
        """
        Reconhece voz em stream contínuo.
        
        Args:
            audio_generator: Generator que produz chunks de bytes
            chunk_duration_s: Duração de cada chunk
            
        Yields:
            SpeechSegment para cada chunk processado
        """
        for audio_chunk in audio_generator:
            segment = self.recognize_chunk(audio_chunk, chunk_duration_s)
            if segment:
                yield segment
```

### 2. `src/lyrics_matcher.py`

**Responsabilidade:** Comparar texto reconhecido com letra conhecida

```python
"""
Fuzzy matching de texto reconhecido com letras de música.
Usado para encontrar a posição atual na letra baseado em STT.
"""

from difflib import SequenceMatcher
from typing import List, Optional, Tuple
from dataclasses import dataclass
import re
import unicodedata

@dataclass
class LyricMatch:
    """Resultado de matching entre texto reconhecido e letra."""
    line_index: int       # Índice da linha na letra
    similarity: float     # 0.0 a 1.0
    matched_text: str     # Texto da linha que deu match
    recognized_text: str  # Texto que foi reconhecido

class LyricsMatcher:
    """
    Encontra a posição atual na letra baseado em texto reconhecido.
    
    Usa fuzzy matching tolerante a:
    - Erros de reconhecimento
    - Palavras parciais
    - Variações de pronúncia/escrita
    """
    
    def __init__(self, min_similarity: float = 0.6):
        """
        Args:
            min_similarity: Similaridade mínima para considerar match (0.0-1.0)
        """
        self.min_similarity = min_similarity
        self._cached_lyrics: List[str] = []
        self._normalized_lyrics: List[str] = []
    
    def set_lyrics(self, lyrics: List[str]) -> None:
        """
        Define letra da música atual.
        
        Args:
            lyrics: Lista de linhas da letra
        """
        self._cached_lyrics = lyrics
        self._normalized_lyrics = [
            self._normalize_text(line) for line in lyrics
        ]
    
    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normaliza texto para matching."""
        # Remover acentos
        text = unicodedata.normalize('NFKD', text)
        text = text.encode('ascii', 'ignore').decode('ascii')
        
        # Lowercase e remover pontuação
        text = text.lower()
        text = re.sub(r'[^\w\s]', '', text)
        
        # Normalizar espaços
        text = ' '.join(text.split())
        
        return text
    
    def find_best_match(
        self, 
        recognized_text: str,
        context_window: int = 10,
        current_index: int = 0
    ) -> Optional[LyricMatch]:
        """
        Encontra melhor match na letra para o texto reconhecido.
        
        Args:
            recognized_text: Texto reconhecido pelo STT
            context_window: Quantas linhas antes/depois considerar
            current_index: Índice atual (otimização: busca próximo primeiro)
            
        Returns:
            LyricMatch com melhor correspondência ou None
        """
        if not self._normalized_lyrics or not recognized_text:
            return None
        
        normalized_input = self._normalize_text(recognized_text)
        
        # Estratégia: buscar primeiro próximo da posição atual
        # depois expandir para resto da letra
        best_match: Optional[LyricMatch] = None
        best_similarity = 0.0
        
        # 1. Buscar em janela próxima da posição atual
        start_idx = max(0, current_index - context_window)
        end_idx = min(len(self._normalized_lyrics), current_index + context_window)
        
        for i in range(start_idx, end_idx):
            similarity = self._calculate_similarity(
                normalized_input, 
                self._normalized_lyrics[i]
            )
            
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = LyricMatch(
                    line_index=i,
                    similarity=similarity,
                    matched_text=self._cached_lyrics[i],
                    recognized_text=recognized_text
                )
        
        # 2. Se não achou bom match, buscar no resto da letra
        if best_similarity < self.min_similarity:
            for i in range(len(self._normalized_lyrics)):
                if start_idx <= i < end_idx:
                    continue  # Já checamos
                    
                similarity = self._calculate_similarity(
                    normalized_input, 
                    self._normalized_lyrics[i]
                )
                
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = LyricMatch(
                        line_index=i,
                        similarity=similarity,
                        matched_text=self._cached_lyrics[i],
                        recognized_text=recognized_text
                    )
        
        # Retornar apenas se similaridade acima do threshold
        if best_match and best_match.similarity >= self.min_similarity:
            return best_match
        
        return None
    
    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """
        Calcula similaridade entre dois textos.
        
        Usa SequenceMatcher (algoritmo de Ratcliff-Obershelp)
        que é bom para texto com erros/variações.
        """
        # Similaridade global
        global_sim = SequenceMatcher(None, text1, text2).ratio()
        
        # Bonus se palavras-chave coincidem
        words1 = set(text1.split())
        words2 = set(text2.split())
        
        if words1 and words2:
            word_overlap = len(words1 & words2) / max(len(words1), len(words2))
            # Média ponderada (70% global, 30% palavras)
            return global_sim * 0.7 + word_overlap * 0.3
        
        return global_sim
    
    def validate_position(
        self, 
        recognized_text: str, 
        expected_index: int,
        tolerance: int = 2
    ) -> bool:
        """
        Valida se posição atual está correta baseado em texto reconhecido.
        
        Args:
            recognized_text: Texto reconhecido
            expected_index: Índice esperado da linha
            tolerance: Quantas linhas de margem permitir
            
        Returns:
            True se validado, False se divergiu
        """
        match = self.find_best_match(
            recognized_text, 
            context_window=tolerance,
            current_index=expected_index
        )
        
        if not match:
            return False
        
        # Verificar se está dentro da tolerância
        return abs(match.line_index - expected_index) <= tolerance
```

### 3. Modificações em `src/worker_headless.py`

**Integrações necessárias:**

```python
# No __init__:
from src.speech_recognition import SpeechRecognizer, SpeechSegment
from src.lyrics_matcher import LyricsMatcher, LyricMatch

class RecognitionWorkerHeadless(threading.Thread):
    def __init__(self, config, audio_capture, recognizer, lyrics_fetcher):
        # ... código existente ...
        
        # NOVO: Configuração STT
        self._stt_enabled = config.getboolean("SpeechSync", "enabled", fallback=False)
        self._stt_mode = config.get("SpeechSync", "mode", fallback="hybrid")  # hybrid|stt_only|timestamp_only
        self._stt_model = config.get("SpeechSync", "model_size", fallback="tiny")
        self._stt_device = config.get("SpeechSync", "device", fallback="cuda")
        self._stt_chunk_duration = config.getfloat("SpeechSync", "chunk_duration_s", fallback=2.5)
        self._stt_validation_interval = config.getint("SpeechSync", "validation_interval_ms", fallback=5000)
        
        # Inicializar componentes STT
        self._speech_recognizer: Optional[SpeechRecognizer] = None
        self._lyrics_matcher = LyricsMatcher(min_similarity=0.6)
        self._stt_thread: Optional[threading.Thread] = None
        self._stt_running = False
        self._current_lyrics_lines: List[str] = []
        self._last_stt_validation: float = 0.0
        
        # Callback para STT
        self._callbacks['stt_recognized'] = []
        self._callbacks['stt_matched'] = []
        self._callbacks['sync_corrected'] = []
        
        # Lazy load do modelo (só quando necessário)
        if self._stt_enabled:
            self._init_stt()
    
    def _init_stt(self) -> None:
        """Inicializa reconhecedor de voz (lazy loading)."""
        try:
            _LOG.info(f"Inicializando Speech Recognizer (modelo={self._stt_model}, device={self._stt_device})")
            self._speech_recognizer = SpeechRecognizer(
                model_size=self._stt_model,
                device=self._stt_device
            )
            _LOG.info("Speech Recognizer inicializado com sucesso")
        except Exception as exc:
            _LOG.error(f"Erro ao inicializar STT: {exc}. Modo STT desabilitado.", exc_info=True)
            self._stt_enabled = False
    
    def _on_lyrics_fetched(self, lyrics_result: Optional[LyricsResult], ...):
        """Handler quando letras são carregadas."""
        # ... código existente ...
        
        # NOVO: Atualizar matcher com nova letra
        if lyrics_result:
            self._current_lyrics_lines = lyrics_result.lines
            if self._stt_enabled:
                self._lyrics_matcher.set_lyrics(lyrics_result.lines)
                _LOG.debug(f"Lyrics matcher atualizado com {len(lyrics_result.lines)} linhas")
    
    def run(self) -> None:
        """Loop principal."""
        # ... código existente ...
        
        # NOVO: Iniciar thread STT se habilitado
        if self._stt_enabled and self._speech_recognizer:
            self._stt_running = True
            self._stt_thread = threading.Thread(
                target=self._stt_loop,
                daemon=True,
                name="SpeechRecognition"
            )
            self._stt_thread.start()
        
        # ... resto do código ...
    
    def _stt_loop(self) -> None:
        """
        Loop de reconhecimento de voz contínuo.
        Roda em paralelo com spectrum_loop e main loop.
        """
        _LOG.info("🎤 Thread de reconhecimento de voz iniciada")
        
        while self._stt_running and not self._stop_flag.is_set():
            if self._pause_flag.is_set() or not self._current_song_key:
                time.sleep(0.5)
                continue
            
            try:
                # Capturar chunk de áudio
                audio_chunk = self._audio.capture(self._stt_chunk_duration)
                
                if not audio_chunk:
                    continue
                
                # Reconhecer voz
                segment = self._speech_recognizer.recognize_chunk(
                    audio_chunk, 
                    self._stt_chunk_duration
                )
                
                if not segment or not segment.text:
                    continue
                
                _LOG.debug(f"🎤 STT: '{segment.text}' (conf={segment.confidence:.2f})")
                self._emit('stt_recognized', segment.text, segment.confidence)
                
                # Fazer matching com letra
                if self._current_lyrics_lines:
                    self._process_stt_match(segment)
                
            except Exception as exc:
                if not self._stop_flag.is_set():
                    _LOG.error(f"Erro no loop STT: {exc}", exc_info=True)
                time.sleep(1.0)  # Cooldown em caso de erro
        
        _LOG.info("Thread de reconhecimento de voz parada")
    
    def _process_stt_match(self, segment: SpeechSegment) -> None:
        """
        Processa resultado do STT e atualiza sincronização.
        
        Comportamento depende do modo:
        - hybrid: Valida posição timestamp e corrige se divergir
        - stt_only: Usa STT como fonte primária de posição
        - timestamp_only: Ignora STT (modo atual)
        """
        # Encontrar match na letra
        current_index = getattr(self, '_current_lyric_index', 0)
        
        match = self._lyrics_matcher.find_best_match(
            segment.text,
            context_window=15,
            current_index=current_index
        )
        
        if not match:
            _LOG.debug(f"STT: Nenhum match encontrado para '{segment.text}'")
            return
        
        _LOG.debug(
            f"STT Match: linha {match.line_index} (sim={match.similarity:.2f}) "
            f"'{match.matched_text}'"
        )
        self._emit('stt_matched', match.line_index, match.similarity, match.matched_text)
        
        # Aplicar correção baseado no modo
        if self._stt_mode == "hybrid":
            self._apply_hybrid_sync_correction(match, current_index)
        elif self._stt_mode == "stt_only":
            self._apply_stt_only_sync(match)
    
    def _apply_hybrid_sync_correction(
        self, 
        match: LyricMatch, 
        current_index: int
    ) -> None:
        """
        Modo híbrido: usa timestamp como base, STT para validação e correção.
        """
        divergence = abs(match.line_index - current_index)
        
        # Se divergência pequena (1-2 linhas), ignorar (pode ser timing natural)
        if divergence <= 1:
            return
        
        # Divergência significativa - corrigir!
        if divergence >= 2 and match.similarity > 0.7:
            _LOG.warning(
                f"⚠️ Sincronização divergiu! Timestamp: linha {current_index}, "
                f"STT: linha {match.line_index} (similaridade {match.similarity:.2f}). "
                f"Corrigindo..."
            )
            
            # Calcular timecode correspondente à linha do STT
            # (assumindo LRC com timestamps)
            corrected_timecode = self._get_timecode_for_line(match.line_index)
            
            if corrected_timecode is not None:
                # Atualizar relógio interno
                self._last_timecode_ms = corrected_timecode
                self._last_timecode_update = time.perf_counter()
                
                # Emitir correção
                self._emit('sync_corrected', corrected_timecode, match.line_index)
                self._emit('timecode_updated', corrected_timecode, time.perf_counter())
                
                _LOG.info(f"✅ Sincronização corrigida para timecode {corrected_timecode}ms")
    
    def _apply_stt_only_sync(self, match: LyricMatch) -> None:
        """
        Modo STT puro: usa apenas STT para determinar posição.
        Usado quando não há timestamps LRC.
        """
        # Criar timecode sintético baseado no índice da linha
        # (assumindo ~3s por linha em média)
        synthetic_timecode = match.line_index * 3000
        
        self._last_timecode_ms = synthetic_timecode
        self._last_timecode_update = time.perf_counter()
        
        self._emit('timecode_updated', synthetic_timecode, time.perf_counter())
    
    def _get_timecode_for_line(self, line_index: int) -> Optional[int]:
        """
        Obtém timecode (ms) correspondente a uma linha da letra.
        Funciona apenas se letra tem timestamps LRC.
        """
        # TODO: Integrar com lyrics_parser.py
        # Por ora, retorna None (implementação futura)
        return None
    
    def stop(self) -> None:
        """Para todas as threads."""
        self._stt_running = False
        # ... código existente ...
```

---

## ⚙️ Configuração (`config.ini`)

Adicionar nova seção:

```ini
[SpeechSync]
# Habilitar sincronização por reconhecimento de voz
enabled = true

# Modo de operação:
#   - hybrid: Timestamp (base) + STT (validação e correção)
#   - stt_only: Apenas STT (para letras sem timestamps)
#   - timestamp_only: Desabilita STT (modo atual)
mode = hybrid

# Modelo Whisper: tiny, base, small, medium, large
# tiny: ~300-500ms, ~75% precisão, ~40MB
# base: ~500-800ms, ~85% precisão, ~75MB
model_size = tiny

# Device: cuda (GPU) ou cpu
# GPU recomendada para latência < 500ms
device = cuda

# Duração de cada chunk de áudio para STT (segundos)
# 2-3s é ideal (muito curto = impreciso, muito longo = latência alta)
chunk_duration_s = 2.5

# Intervalo de validação STT em modo hybrid (ms)
# A cada X ms, valida se timestamp está correto
validation_interval_ms = 5000

# Similaridade mínima para considerar match (0.0-1.0)
min_similarity = 0.65

# Correção automática: divergência mínima (linhas) para corrigir
auto_correct_threshold = 2
```

---

## 📊 Fluxo de Dados

### Modo Híbrido (Recomendado)

```
T=0s    Música inicia
        │
T=2s    ├─► Fingerprinting identifica música
        │   └─► Busca letra com timestamps LRC
        │
T=3s    │   Letra carregada
        │   ├─► LyricsMatcher inicializado
        │   └─► Timecode inicial = 3000ms
        │
T=3.05s ├─► Spectrum loop: timecode interpolado = 3050ms
        │   └─► UI mostra linha 0
        │
T=5.5s  ├─► STT reconhece "as palavras da primeira linha"
        │   ├─► Matcher: 85% similar com linha 0
        │   ├─► Divergência = 0 linhas
        │   └─► ✅ Validado, sem correção
        │
T=8s    ├─► Spectrum loop: timecode interpolado = 8000ms
        │   └─► UI mostra linha 1
        │
T=10.5s ├─► STT reconhece "segunda linha cantada"
        │   ├─► Matcher: 90% similar com linha 1
        │   └─► ✅ Validado
        │
T=35s   │   Usuário pula música (seek +30s)
        │   Fingerprinting não detecta imediatamente...
        │   Spectrum loop continua com timecode desatualizado
        │
T=37.5s ├─► STT reconhece "linha do meio da música"
        │   ├─► Matcher: 88% similar com linha 15
        │   ├─► Divergência = |15 - 5| = 10 linhas ⚠️
        │   ├─► Auto-correção ativada!
        │   └─► Timecode atualizado para linha 15
        │
T=38s   └─► UI sincronizada novamente ✅
```

### Modo STT Puro (Sem timestamps)

```
T=0s    Música inicia
        │
T=2s    ├─► Fingerprinting identifica música
        │   └─► Busca letra (SEM timestamps)
        │
T=3s    │   Letra carregada (plain text)
        │   └─► LyricsMatcher inicializado
        │
T=5.5s  ├─► STT reconhece "primeira linha"
        │   ├─► Matcher: 82% similar com linha 0
        │   └─► Timecode sintético = 0ms
        │
T=8s    ├─► STT reconhece "segunda linha"
        │   ├─► Matcher: 88% similar com linha 1
        │   └─► Timecode sintético = 3000ms (linha 1 × 3s)
        │
T=10.5s ├─► STT reconhece "terceira linha"
        │   ├─► Matcher: 85% similar com linha 2
        │   └─► Timecode sintético = 6000ms
        │
        ... continua baseado apenas em STT
```

---

## 📦 Dependências

### Python Packages (adicionar a `requirements.txt`)

```txt
# Speech-to-Text
faster-whisper==1.1.0        # ~5MB (código), modelos baixados separadamente
ctranslate2==4.5.0           # Backend otimizado do faster-whisper
numpy>=1.24.0                # Processamento de áudio

# Alternativa CPU-only (comentado por padrão):
# whisper-cpp-python==0.2.0  # Binding Python do whisper.cpp
```

### Modelos Whisper

Baixados automaticamente no primeiro uso para `~/.cache/huggingface/`:

- **tiny**: ~39 MB (EN), ~74 MB (multilingual)
- **base**: ~74 MB (EN), ~142 MB (multilingual)
- **small**: ~244 MB (multilingual)

**Usar multilingual** para português brasileiro.

---

## 🧪 Testes e Validação

### Cenários de Teste

1. **Música com LRC preciso** (modo hybrid)
   - ✅ Deve manter sincronização timestamp
   - ✅ STT valida sem correções desnecessárias
   
2. **Música sem LRC** (modo stt_only)
   - ✅ Sincroniza baseado apenas em STT
   - ⚠️ Pode pular linhas rápidas ou instrumentais

3. **Seek manual** (modo hybrid)
   - ✅ STT detecta divergência e corrige em 2-5s

4. **Música com ruído/efeitos**
   - ⚠️ STT pode ter baixa precisão (<60%)
   - ✅ Threshold de similaridade previne correções erradas

5. **Rap rápido/canto complexo**
   - ⚠️ Latência pode aumentar para 1-2s
   - ⚠️ Precisão reduzida (~65-70%)

### Métricas de Sucesso

| Métrica | Target | Medição |
|---------|--------|---------|
| Latência STT | < 700ms | tempo entre captura e emissão de `stt_recognized` |
| Precisão matching | > 75% | % de linhas corretamente identificadas |
| Taxa de correção válida | > 90% | correções que melhoram sync vs pioram |
| Uso de CPU (STT ativo) | < 50% | média durante reprodução |
| Uso de GPU VRAM | < 1GB | para modelo tiny/base |

---

## 🚀 Plano de Implementação

### Fase 1: Setup e Infraestrutura (2-3 dias)

- [x] Pesquisa de bibliotecas STT
- [ ] Criar `src/speech_recognition.py` (classe base)
- [ ] Criar `src/lyrics_matcher.py` (fuzzy matching)
- [ ] Adicionar configurações em `config.ini`
- [ ] Testes unitários dos novos módulos

### Fase 2: Integração com Worker (3-4 dias)

- [ ] Modificar `src/worker_headless.py`:
  - [ ] Adicionar inicialização lazy do STT
  - [ ] Criar `_stt_loop()` thread
  - [ ] Integrar `_process_stt_match()`
  - [ ] Implementar modo `timestamp_only` (baseline)
  
- [ ] Testes de integração:
  - [ ] STT thread roda em paralelo
  - [ ] Callbacks emitidos corretamente
  - [ ] Sem bloqueios ou race conditions

### Fase 3: Modo STT Puro (2-3 dias)

- [ ] Implementar `_apply_stt_only_sync()`
- [ ] Suporte a letras sem timestamps
- [ ] Timecode sintético baseado em índices
- [ ] Testes com músicas plain text

### Fase 4: Modo Híbrido (4-5 dias)

- [ ] Implementar `_apply_hybrid_sync_correction()`
- [ ] Detecção de divergência
- [ ] Auto-correção de timecode
- [ ] Integração com `lyrics_parser.py` para obter timestamps
- [ ] Testes de correção de seeks

### Fase 5: Otimizações e Polish (3-4 dias)

- [ ] Tuning de parâmetros:
  - [ ] Chunk duration
  - [ ] Similarity threshold
  - [ ] Correction threshold
  
- [ ] Otimizações de performance:
  - [ ] Resampling eficiente 44.1kHz → 16kHz
  - [ ] Caching de modelo Whisper
  - [ ] Reduzir alocações de memória
  
- [ ] UI/UX:
  - [ ] Indicador visual de modo STT ativo
  - [ ] Mostrar confiança do STT
  - [ ] Notificar correções ("Sincronização ajustada")

### Fase 6: Testes Finais e Documentação (2 dias)

- [ ] Testes end-to-end com músicas variadas
- [ ] Benchmark de performance
- [ ] Documentação de uso
- [ ] Atualizar README com feature STT

**Total estimado: 16-21 dias de desenvolvimento**

---

## ⚠️ Limitações Conhecidas

1. **Latência mínima ~300-500ms**
   - Whisper tiny é o mais rápido mas ainda tem latência perceptível
   - Para sincronização < 100ms, fingerprinting local seria necessário

2. **Precisão com música variável**
   - Voz cantada != voz falada (modelos STT são treinados em fala)
   - Música muito processada/distorcida reduz precisão para ~60%

3. **GPU recomendada**
   - CPU possível mas latência sobe para 1-2s (inaceitável para sync em tempo real)
   - GPU integrada (Intel/AMD) não é suficiente, precisa dGPU (NVIDIA RTX)

4. **Consumo de recursos**
   - Modelo tiny usa ~500MB VRAM + ~20-30% CPU
   - Captura contínua de áudio (chunks de 2-3s) aumenta uso de memória

5. **Idioma fixo (PT-BR)**
   - Por ora, assumindo português
   - Para multi-idioma, precisaria detectar língua primeiro (latência extra)

6. **Sem detecção de pause**
   - STT não diferencia música pausada vs silêncio musical
   - Pode tentar reconhecer em pausas longas (desperdício de recursos)

---

## 🔮 Melhorias Futuras

### Curto Prazo

- **Detecção de idioma automática:** Usar `language="auto"` no Whisper
- **Resampling otimizado:** libresample ou torchaudio para 44.1k → 16k
- **Cache de matches:** Evitar re-matching de linhas já vistas

### Médio Prazo

- **Fingerprinting local (Chromaprint/Dejavu):**
  - Complementar STT com position tracking preciso
  - Detectar seeks instantaneamente
  
- **VAD (Voice Activity Detection) melhorado:**
  - Não rodar STT em partes instrumentais
  - Economia de ~30-50% de recursos

### Longo Prazo

- **Fine-tuning do Whisper:**
  - Treinar modelo específico para letras de música
  - Dataset: pares (áudio musical, letra)
  - Potencial de +10-15% de precisão
  
- **Modelo local + LLM:**
  - Usar embeddings de texto para matching semântico
  - Ex: "você é meu amor" ≈ "tu és meu bem"

---

## 📚 Referências

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Whisper paper (OpenAI)](https://arxiv.org/abs/2212.04356)
- [LRC Format Specification](https://en.wikipedia.org/wiki/LRC_(file_format))
- [Fuzzy String Matching in Python](https://www.datacamp.com/tutorial/fuzzy-string-python)

---

**Documento criado em:** 20 de abril de 2026  
**Versão:** 1.0.0  
**Status:** 📋 Especificação completa - Pronto para implementação
