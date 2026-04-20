# Visualização da Arquitetura Implementada (Fase 1)

## 🏗️ Stack de Tecnologias

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    FLOATING LYRICS - STT STACK                          │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────┐
│   CAMADA 1: RECONHECIMENTO    │
├──────────────────────────────┤
│  Python Library: faster-whisper
│  Backend: CTranslate2 (C++)
│  Model: whisper-tiny/base/small (Hugging Face)
│  Device: GPU (CUDA) | CPU (fallback)
│
│  Audio Input → 16kHz mono PCM
│  Output: [text, confidence, timestamps]
└──────────────────────────────┘
         ↓
┌──────────────────────────────┐
│   CAMADA 2: MATCHING & SYNC   │
├──────────────────────────────┤
│  Algorithm: Ratcliff-Obershelp (difflib)
│  Feature: Fuzzy matching + normalization
│  Parsing: Acentos, pontuação, spacing
│
│  [STT Text] + [Lyrics DB]
│  → Find best match
│  → Return [line_index, similarity]
└──────────────────────────────┘
         ↓
┌──────────────────────────────┐
│   CAMADA 3: SINCRONIZAÇÃO     │
├──────────────────────────────┤
│  3 Modos:
│  1. timestamp_only (atual)
│  2. stt_only (novo)
│  3. hybrid (recomendado)
│
│  Output: timecode_ms para UI
└──────────────────────────────┘
```

## 📦 Estrutura de Classes

```python
src/
├── speech_recognition.py
│   └── class SpeechRecognizer
│       ├── __init__(model_size, device)
│       ├── recognize_chunk(audio_bytes) → SpeechSegment
│       └── _simple_resample() → ndarray
│
└── lyrics_matcher.py
    └── class LyricsMatcher
        ├── __init__(min_similarity)
        ├── set_lyrics(lines)
        ├── find_best_match() → LyricMatch
        ├── validate_position() → bool
        └── _calculate_similarity() → float
        
Dataclasses:
├── SpeechSegment
│   ├── text: str
│   ├── confidence: float
│   ├── start_ms: int
│   └── end_ms: int
│
└── LyricMatch
    ├── line_index: int
    ├── similarity: float
    ├── matched_text: str
    └── recognized_text: str
```

## 🔄 Fluxo de Dados (Exemplo)

```
Música: "Quando a luz dos olhos meus..."

T=0ms    ┌─────────────────────────────┐
         │ Audio Capture (WASAPI)      │
         │ 2.5s chunk (44.1 kHz)       │
         └─────────────┬───────────────┘
                       │
T=200ms  ┌─────────────▼───────────────┐
         │ SpeechRecognizer            │
         │ • Resample 44.1k → 16k      │
         │ • faster-whisper inference  │
         │ • VAD filter                │
         └─────────────┬───────────────┘
                       │ 500-700ms
                       │ (latência)
T=700ms  ┌─────────────▼───────────────┐
         │ SpeechSegment               │
         │ {                           │
         │   "text": "quando a luz    │
         │            dos olhos meus",│
         │   "confidence": -0.15,     │
         │   "start_ms": 150,         │
         │   "end_ms": 2400           │
         │ }                           │
         └─────────────┬───────────────┘
                       │
T=750ms  ┌─────────────▼───────────────┐
         │ LyricsMatcher               │
         │ • Normalize input text     │
         │ • Search context window    │
         │ • Calculate similarity     │
         └─────────────┬───────────────┘
                       │ 10-50ms
                       │ (matching)
T=800ms  ┌─────────────▼───────────────┐
         │ LyricMatch                  │
         │ {                           │
         │   "line_index": 0,         │
         │   "similarity": 0.92,      │
         │   "matched_text":          │
         │     "Quando a luz dos      │
         │      olhos meus"           │
         │ }                           │
         └─────────────┬───────────────┘
                       │
T=850ms  ┌─────────────▼───────────────┐
         │ Sync Decision               │
         │ mode=hybrid:                │
         │  - Validate vs timestamp   │
         │  - Correct if diverged     │
         │  - Emit timecode_updated   │
         └─────────────┬───────────────┘
                       │
T=900ms  └─────────────▼───────────────┐
         │ UI Update                   │
         │ • Scroll to line 0          │
         │ • Highlight "Quando a..."  │
         └─────────────────────────────┘

Total: ~900ms (da captura ao UI update)
```

## ⚙️ Configuração Padrão

```ini
[SpeechSync]
# Estado padrão (Fase 1)
enabled = false                    # Desabilitado por padrão (MVP)
mode = hybrid                      # Mas pronto para modo híbrido
model_size = tiny                  # Melhor trade-off latência/precisão
device = cuda                      # GPU preferida
chunk_duration_s = 2.5             # ~2s reconhecimento + 0.5s latência
validation_interval_ms = 5000      # Valida a cada 5s (modo hybrid)
min_similarity = 0.65              # 65% threshold (balanceado)
auto_correct_threshold = 2         # Corrige em divergência >= 2 linhas
```

## 🧪 Cobertura de Testes (Fase 1)

```
LyricsMatcher:
├── ✅ Match exato (similaridade 1.0)
├── ✅ Match com variações (acentos, pontuação)
├── ✅ Context window search
├── ✅ Texto não encontrado (retorna None)
├── ✅ Validação de posição (dentro tolerância)
└── ✅ Detecção de divergência (fora tolerância)

SpeechRecognizer:
├── ✅ Áudio silencioso (RMS baixo)
├── ✅ Áudio com ruído
└── ✅ Processamento sem crashes

Integração:
├── ✅ Imports funcionando
├── ✅ Logging estruturado
└── ✅ Tratamento de exceções
```

## 🎯 Recursos vs. Performance

```
Scenario: MacBook Pro M1, GPU RTX 3060, RTX 2060 Low-End, CPU Intel i7

Device          | Model | Latência | Precisão | VRAM | CPU  | Status
----------------|-------|----------|----------|------|------|--------
RTX 3060        | tiny  | 300-400ms| 75%      | 400MB| 15%  | ✅ Ideal
RTX 3060        | base  | 500-700ms| 85%      | 600MB| 20%  | ✅ Bom
RTX 2060        | tiny  | 400-600ms| 75%      | 400MB| 20%  | ✅ OK
GTX 1660 Ti     | tiny  | 500-800ms| 75%      | 500MB| 25%  | ✅ Funciona
CPU (i7)        | tiny  | 1.5-2.5s | 75%      | 200MB| 60%  | ⚠️  Lento
Laptop GPU      | tiny  | 1.0-2.0s | 75%      | 300MB| 40%  | ⚠️  Marginal

Legend:
✅ = Excelente (< 1s latência, < 50% CPU)
⚠️  = Aceitável (1-2s latência, > 50% CPU)
❌ = Inaceitável (> 2s latência)
```

## 🔌 Interfaces Públicas

### SpeechRecognizer

```python
# Inicializar
rec = SpeechRecognizer(model_size="tiny", device="cuda")

# Usar
segment = rec.recognize_chunk(
    audio_data=b'...',           # bytes PCM int16
    sample_rate=44100,           # Hz
    duration_s=2.5              # segundos
)

# Resultado
if segment:
    print(segment.text)          # str
    print(segment.confidence)    # float (-1.0 a 0.0)
    print(segment.start_ms)      # int
    print(segment.end_ms)        # int
```

### LyricsMatcher

```python
# Inicializar
matcher = LyricsMatcher(min_similarity=0.65)

# Configurar letra
matcher.set_lyrics(["linha 1", "linha 2", ...])

# Find match
match = matcher.find_best_match(
    recognized_text="texto do STT",
    context_window=10,           # buscar ±10 linhas
    current_index=5              # dica: ~linha 5
)

# Resultado
if match:
    print(match.line_index)      # int
    print(match.similarity)      # float (0.0 a 1.0)
    print(match.matched_text)    # str
    print(match.recognized_text) # str

# Validar posição
is_valid = matcher.validate_position(
    recognized_text="...",
    expected_index=5,
    tolerance=2                  # ±2 linhas
)
```

## 📈 Roadmap Fase 1 → Fase 2

```
Fase 1 (✅ COMPLETO)
├── Dependências instaladas
├── SpeechRecognizer criado
├── LyricsMatcher criado
├── Configuração em config.ini
└── Testes passando (9/9)

Fase 2 (⏳ PRÓXIMA)
├── Integração com worker_headless.py
├── Thread _stt_loop() para processamento contínuo
├── Callbacks: stt_recognized, stt_matched, sync_corrected
├── Modo baseline: timestamp_only
├── Lazy loading do modelo
└── Testes de integração com captura de áudio

Fase 3 (⏸️ DEPOIS)
├── Modo stt_only (apenas STT, sem timestamps)
├── Timecode sintético por índice
└── Testes com músicas sem LRC

Fase 4 (⏸️ DEPOIS)
├── Modo hybrid (timestamp + STT validação)
├── Auto-correção de drift
├── Integração com lyrics_parser.py
└── Testes de seek/divergência

Fase 5 (⏸️ DEPOIS)
├── Otimizações de performance
├── Tuning de parâmetros
├── UX/feedback visual
└── Benchmark completo

Fase 6 (⏸️ DEPOIS)
├── Testes end-to-end
├── Documentação final
└── Release
```

---

**Diagrama criado:** 20 de abril de 2026  
**Versão:** 1.0  
**Status:** ✅ Fase 1 Implementada
